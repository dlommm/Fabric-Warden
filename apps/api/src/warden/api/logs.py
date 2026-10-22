"""Log Management: search and summarise syslog events, receiver status, demo feed."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.models import Client, Controller, Device, LogEvent
from warden.security.rbac import P
from warden.syslog import seed_demo

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_module("logs"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.SOC_MANAGE)
SEVERITY = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]


def _row(e: LogEvent, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": e.id,
        "received_at": e.received_at,
        "logged_at": e.logged_at,
        "source_ip": e.source_ip,
        "host": e.host,
        "app": e.app,
        "severity": e.severity,
        "severity_name": SEVERITY[e.severity] if 0 <= e.severity < 8 else str(e.severity),
        "category": e.category,
        "message": e.message,
        "fmt": e.fmt,
        "fields": e.fields,
        "device": {"id": e.device_id, "name": names.get(e.device_id or "")} if e.device_id else None,
        "client": {"id": e.client_id, "name": names.get(e.client_id or "")} if e.client_id else None,
        "is_demo": e.is_demo,
    }


@router.get("")
async def search(
    db: DB,
    principal: Reader,
    q: str | None = None,
    category: Literal["firewall", "ids", "auth", "system", "other"] | None = None,
    max_severity: Annotated[int, Query(ge=0, le=7)] = 7,
    source_ip: str | None = None,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict:
    stmt = select(LogEvent).where(LogEvent.received_at >= utcnow() - timedelta(hours=hours), LogEvent.severity <= max_severity)
    if category:
        stmt = stmt.where(LogEvent.category == category)
    if source_ip:
        stmt = stmt.where(LogEvent.source_ip == source_ip)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(LogEvent.message).like(like),
                func.lower(LogEvent.host).like(like),
                func.lower(LogEvent.app).like(like),
                LogEvent.source_ip.like(like),
            )
        )
    rows = list(await db.scalars(stmt.order_by(LogEvent.received_at.desc()).limit(limit)))
    names = {d.id: d.name for d in await db.scalars(select(Device).where(Device.id.in_({r.device_id for r in rows if r.device_id})))}
    names |= {
        c.id: c.alias or c.name for c in await db.scalars(select(Client).where(Client.id.in_({r.client_id for r in rows if r.client_id})))
    }
    return {"items": [_row(r, names) for r in rows], "limit": limit}


@router.get("/stats")
async def stats(db: DB, principal: Reader, hours: Annotated[int, Query(ge=1, le=168)] = 24) -> dict:
    since = utcnow() - timedelta(hours=hours)
    by_cat = dict(
        (await db.execute(select(LogEvent.category, func.count()).where(LogEvent.received_at >= since).group_by(LogEvent.category))).all()
    )
    by_sev = dict(
        (await db.execute(select(LogEvent.severity, func.count()).where(LogEvent.received_at >= since).group_by(LogEvent.severity))).all()
    )
    top = (
        await db.execute(
            select(LogEvent.source_ip, func.max(LogEvent.host), func.count().label("n"))
            .where(LogEvent.received_at >= since)
            .group_by(LogEvent.source_ip)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    rows = list(await db.scalars(select(LogEvent.received_at).where(LogEvent.received_at >= since)))
    bucket = max(60, hours * 3600 // 48)
    # Every bucket in the window, empty ones included, so quiet periods read as quiet.
    first = int(since.timestamp()) // bucket * bucket
    series: dict[int, int] = {first + i * bucket: 0 for i in range((int(utcnow().timestamp()) - first) // bucket + 1)}
    for ts in rows:
        k = int(ts.timestamp()) // bucket * bucket
        series[k] = series.get(k, 0) + 1
    return {
        "hours": hours,
        "total": sum(by_cat.values()),
        "by_category": by_cat,
        "by_severity": {SEVERITY[k]: v for k, v in sorted(by_sev.items()) if 0 <= k < 8},
        "top_sources": [{"source_ip": ip, "host": host, "count": n} for ip, host, n in top],
        "bucket_seconds": bucket,
        "series": [{"t": k, "count": v} for k, v in sorted(series.items())],
        "since": since,
    }


@router.get("/status")
async def receiver_status(state: State, principal: Reader) -> dict:
    ing = state.syslog
    return {
        "enabled": bool(state.settings.syslog_port),
        "listening": ing.listening,
        "error": ing.error,
        "port": state.settings.syslog_port or None,
        "received": ing.received,
        "stored": ing.stored,
        "dropped": ing.dropped,
        "last_received_at": ing.last_at,
        "retention_days": state.settings.log_retention_days,
    }


@router.post("/demo", status_code=status.HTTP_201_CREATED)
async def demo(db: DB, state: State, principal: Manager) -> dict:
    if not await db.scalar(select(Controller).where(Controller.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect the demo controller first; demo logs come from its devices.")
    await db.commit()  # the ingest writes in its own session
    n = await seed_demo(state, state.syslog)
    await audit.record(db, principal.actor, "logs.demo.seed", detail={"events": n})
    return {"stored": n}


@router.delete("/demo")
async def remove_demo(db: DB, principal: Manager) -> dict:
    n = (await db.execute(delete(LogEvent).where(LogEvent.is_demo.is_(True)))).rowcount or 0
    await audit.record(db, principal.actor, "logs.demo.remove", detail={"events": n})
    return {"removed": n}


@router.get("/connections")
async def connections(db: DB, principal: Reader, hours: Annotated[int, Query(ge=1, le=168)] = 24, client_id: str | None = None) -> dict:
    """Firewall log lines read as connections: who talks to what, on which port, and which rules fire."""
    stmt = (
        select(LogEvent)
        .where(LogEvent.category == "firewall", LogEvent.received_at >= utcnow() - timedelta(hours=hours))
        .order_by(LogEvent.received_at.desc())
        .limit(50_000)
    )
    if client_id:
        stmt = stmt.where(LogEvent.client_id == client_id)
    rows = [r for r in await db.scalars(stmt) if r.fields.get("src")]
    names = {
        c.id: c.alias or c.name for c in await db.scalars(select(Client).where(Client.id.in_({r.client_id for r in rows if r.client_id})))
    }
    sources: dict[str, dict[str, Any]] = {}
    dests: dict[str, dict[str, int]] = {}
    ports: dict[str, dict[str, int]] = {}
    rules: dict[str, dict[str, Any]] = {}
    totals = {"allow": 0, "block": 0, "unknown": 0}
    for r in rows:
        f = r.fields
        action = f.get("action", "unknown")
        totals[action] = totals.get(action, 0) + 1
        s = sources.setdefault(
            f["src"],
            {
                "src": f["src"],
                "client": {"id": r.client_id, "name": names.get(r.client_id or "")} if r.client_id else None,
                "allow": 0,
                "block": 0,
                "unknown": 0,
            },
        )
        s[action] = s.get(action, 0) + 1
        d = dests.setdefault(f.get("dst", "?"), {"allow": 0, "block": 0, "unknown": 0})
        d[action] = d.get(action, 0) + 1
        if f.get("dpt"):
            p = ports.setdefault(f"{f.get('proto', '?')}/{f['dpt']}", {"allow": 0, "block": 0, "unknown": 0})
            p[action] = p.get(action, 0) + 1
        key = f.get("descr") or f.get("rule_id") or "unnamed"
        ru = rules.setdefault(
            key, {"rule": key, "rule_id": f.get("rule_id"), "ruleset": f.get("ruleset"), "action": action, "hits": 0, "sources": set()}
        )
        ru["hits"] += 1
        ru["sources"].add(f["src"])
    total = lambda x: x["allow"] + x["block"] + x["unknown"]  # noqa: E731
    return {
        "hours": hours,
        "lines": len(rows),
        "totals": totals,
        "sources": sorted(sources.values(), key=total, reverse=True)[:15],
        "destinations": sorted(({"dst": k, **v} for k, v in dests.items()), key=total, reverse=True)[:15],
        "ports": sorted(({"port": k, **v} for k, v in ports.items()), key=total, reverse=True)[:15],
        "rules": [
            {**{k: v for k, v in r.items() if k != "sources"}, "sources": len(r["sources"])}
            for r in sorted(rules.values(), key=lambda r: r["hits"], reverse=True)[:20]
        ],
        "note": "Built from firewall log lines: only connections a logging policy recorded are counted, and there are no byte counts.",
    }

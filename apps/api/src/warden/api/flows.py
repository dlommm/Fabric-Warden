"""Traffic & Flow Analytics: top talkers, conversations and ports from NetFlow/IPFIX, collector status, demo feed."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, delete, func, or_, select

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.flows import PROTOCOLS, scope, seed_demo
from warden.models import Client, Controller, Device, FlowRollup
from warden.security.rbac import P

router = APIRouter(prefix="/api/flows", tags=["flows"], dependencies=[Depends(require_module("flows"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.SOC_MANAGE)

F = FlowRollup
# Counts scaled up by the sampling rate the exporter reported (or the configured default); unknown counts as 1.
SCALE = case((F.sampling > 1, F.sampling), else_=1)
EST_BYTES = func.sum(F.bytes * SCALE)
EST_PACKETS = func.sum(F.packets * SCALE)


def _proto(n: int) -> str:
    return PROTOCOLS.get(n, str(n))


async def _names(db: DB, ips: set[str]) -> dict[str, dict[str, Any]]:
    """Current inventory names for addresses: devices first, then clients."""
    if not ips:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for c in await db.scalars(select(Client).where(Client.ip_address.in_(ips), Client.present.is_(True))):
        out[c.ip_address or ""] = {"type": "client", "id": c.id, "name": c.alias or c.name}
    for d in await db.scalars(select(Device).where(Device.ip_address.in_(ips), Device.present.is_(True))):
        out[d.ip_address or ""] = {"type": "device", "id": d.id, "name": d.name}
    return out


def _addr(ip: str, names: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"ip": ip, "scope": scope(ip), "entity": names.get(ip)}


@router.get("/summary")
async def summary(
    db: DB,
    principal: Reader,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    client_id: str | None = None,
    ip: str | None = None,
) -> dict:
    since = utcnow() - timedelta(hours=hours)
    where = [F.bucket >= since]
    if client_id:
        where.append(or_(F.src_client_id == client_id, F.dst_client_id == client_id))
    if ip:
        where.append(or_(F.src_ip == ip, F.dst_ip == ip))

    totals = (
        await db.execute(
            select(
                EST_BYTES,
                EST_PACKETS,
                func.sum(F.flows),
                func.count(func.distinct(F.src_ip + ">" + F.dst_ip)),
                func.sum(case((F.sampling == 0, 1), else_=0)),
                func.count(),
            ).where(*where)
        )
    ).one()
    rates = sorted(r for r in await db.scalars(select(F.sampling).where(*where, F.sampling > 1).distinct()))

    bucket = max(60, hours * 3600 // 48)
    first = int(since.timestamp()) // bucket * bucket
    series: dict[int, list[int]] = {first + i * bucket: [0, 0] for i in range((int(utcnow().timestamp()) - first) // bucket + 1)}
    for t, b, p in (await db.execute(select(F.bucket, EST_BYTES, EST_PACKETS).where(*where).group_by(F.bucket))).all():
        k = int(t.timestamp()) // bucket * bucket
        acc = series.setdefault(k, [0, 0])
        acc[0] += int(b or 0)
        acc[1] += int(p or 0)

    talkers: dict[str, dict[str, int]] = {}
    for col, side in ((F.src_ip, "sent"), (F.dst_ip, "received")):
        for addr, b in (await db.execute(select(col, EST_BYTES).where(*where).group_by(col))).all():
            t = talkers.setdefault(addr, {"sent": 0, "received": 0})
            t[side] += int(b or 0)
    top_talkers = sorted(talkers.items(), key=lambda kv: kv[1]["sent"] + kv[1]["received"], reverse=True)[:15]

    convs = (
        await db.execute(
            select(F.src_ip, F.dst_ip, F.protocol, F.port, EST_BYTES, EST_PACKETS, func.sum(F.flows), func.max(F.bucket))
            .where(*where)
            .group_by(F.src_ip, F.dst_ip, F.protocol, F.port)
            .order_by(EST_BYTES.desc())
            .limit(25)
        )
    ).all()
    ports = (
        await db.execute(
            select(F.protocol, F.port, EST_BYTES, func.sum(F.flows), func.count(func.distinct(F.src_ip)))
            .where(*where)
            .group_by(F.protocol, F.port)
            .order_by(EST_BYTES.desc())
            .limit(15)
        )
    ).all()
    by_version = dict((await db.execute(select(F.ip_version, EST_BYTES).where(*where).group_by(F.ip_version))).all())
    by_proto = (await db.execute(select(F.protocol, EST_BYTES).where(*where).group_by(F.protocol).order_by(EST_BYTES.desc()))).all()
    demo = await db.scalar(select(func.count()).select_from(F).where(*where, F.is_demo.is_(True))) or 0

    names = await _names(db, {a for a, _ in top_talkers} | {c[0] for c in convs} | {c[1] for c in convs})
    return {
        "hours": hours,
        "since": since,
        "totals": {
            "bytes": int(totals[0] or 0),
            "packets": int(totals[1] or 0),
            "flows": int(totals[2] or 0),
            "conversations": int(totals[3] or 0),
        },
        "sampling": {"rates": rates, "unreported_rows": int(totals[4] or 0), "rows": int(totals[5] or 0)},
        "has_demo": demo > 0,
        "bucket_seconds": bucket,
        "series": [{"t": k, "bytes": v[0], "packets": v[1]} for k, v in sorted(series.items())],
        "talkers": [{**_addr(a, names), **v} for a, v in top_talkers],
        "conversations": [
            {
                "src": _addr(s, names),
                "dst": _addr(d, names),
                "protocol": _proto(pr),
                "port": po,
                "bytes": int(b or 0),
                "packets": int(p or 0),
                "flows": int(n or 0),
                "last_seen": last,
            }
            for s, d, pr, po, b, p, n, last in convs
        ],
        "ports": [
            {"protocol": _proto(pr), "port": po, "bytes": int(b or 0), "flows": int(n or 0), "sources": int(srcs or 0)}
            for pr, po, b, n, srcs in ports
        ],
        "by_ip_version": {f"IPv{k}": int(v or 0) for k, v in sorted(by_version.items())},
        "by_protocol": [{"protocol": _proto(pr), "bytes": int(b or 0)} for pr, b in by_proto],
    }


@router.get("/status")
async def collector_status(state: State, principal: Reader) -> dict:
    c = state.flows
    s = state.settings
    return {
        "enabled": bool(s.flow_port),
        "listening": c.listening,
        "error": c.error,
        "port": s.flow_port or None,
        "datagrams": c.datagrams,
        "flows": c.flows,
        "stored": c.stored,
        "dropped": c.dropped,
        "malformed": c.malformed,
        "rejected": c.rejected,
        "last_received_at": c.last_at,
        "retention_days": s.flow_retention_days,
        "assumed_sampling": s.flow_sampling_rate or None,
        "allowed_exporters": s.flow_allowed_exporters,
        "exporters": [
            {
                "address": e.address,
                "domain": e.domain,
                "version": e.version,
                "sampling": e.sampling or None,
                "packets": e.packets,
                "flows": e.flows,
                "undecoded_sets": e.undecoded,
                "templates": len(e.templates),
                "fields": e.fields_seen(),
                "last_at": e.last_at,
            }
            for e in sorted(c.decoder.exporters.values(), key=lambda e: (e.address, e.domain))
        ],
    }


@router.post("/demo", status_code=status.HTTP_201_CREATED)
async def demo(db: DB, state: State, principal: Manager) -> dict:
    if not await db.scalar(select(Controller).where(Controller.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect the demo controller first; demo flows come from its clients.")
    await db.commit()  # the collector writes in its own session
    n = await seed_demo(state)
    await audit.record(db, principal.actor, "flows.demo.seed", detail={"rows": n})
    return {"stored": n}


@router.delete("/demo")
async def remove_demo(db: DB, principal: Manager) -> dict:
    n = (await db.execute(delete(FlowRollup).where(FlowRollup.is_demo.is_(True)))).rowcount or 0
    await audit.record(db, principal.actor, "flows.demo.remove", detail={"rows": n})
    return {"removed": n}

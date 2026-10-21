"""Investigation workbench: resolve an IP, MAC or name to everything known about it, on one timeline."""

from __future__ import annotations

import ipaddress
import re
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from warden.api.deps import DB, Allowed, require_module
from warden.db import utcnow
from warden.models import Alert, AuditRecord, Client, ClientSighting, Device, EventRecord, Host, LogEvent, SocCase, Workload
from warden.security.rbac import P

router = APIRouter(prefix="/api/investigate", tags=["investigations"], dependencies=[Depends(require_module("investigations"))])

Reader = Allowed(P.INVENTORY_READ)
SYSLOG_SEVERITY = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]
MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


@router.get("")
async def investigate(
    db: DB, principal: Reader, q: Annotated[str, Query(min_length=2, max_length=128)], days: Annotated[int, Query(ge=1, le=90)] = 7
) -> dict:
    term = q.strip()
    low = term.lower()
    is_ip = True
    try:
        ipaddress.ip_address(term)
    except ValueError:
        is_ip = False
    mac = low.replace("-", ":") if MAC_RE.match(low) else None
    since = utcnow() - timedelta(days=days)

    def match(model: Any, *, ip: Any = None, mac_col: Any = None, names: list[Any]) -> Any:
        conds = []
        if is_ip and ip is not None:
            conds.append(ip == term)
        if mac and mac_col is not None:
            conds.append(mac_col == mac)
        if not is_ip and not mac:
            conds += [func.lower(n) == low for n in names]
        return select(model).where(or_(*conds)) if conds else None

    clients = list(await db.scalars(match(Client, ip=Client.ip_address, mac_col=Client.mac_address, names=[Client.name, Client.alias])))
    devices = list(await db.scalars(match(Device, ip=Device.ip_address, mac_col=Device.mac_address, names=[Device.name])))
    host_q = match(Host, ip=Host.ip_address, names=[Host.name, Host.hostname])
    hosts = (
        list(await db.scalars(host_q))
        if host_q is not None
        else [h for h in await db.scalars(select(Host)) if mac and mac in (h.mac_addresses or [])]
    )
    workloads = [
        w
        for w in await db.scalars(select(Workload).where(Workload.present.is_(True)))
        if (is_ip and term in (w.ip_addresses or []))
        or (mac and mac in (w.mac_addresses or []))
        or (not is_ip and not mac and w.name.lower() == low)
    ]
    # A matched client brings its own address and MAC into the search.
    ips = {term} if is_ip else set()
    ips |= (
        {c.ip_address for c in clients if c.ip_address}
        | {d.ip_address for d in devices if d.ip_address}
        | {h.ip_address for h in hosts if h.ip_address}
    )
    for w in workloads:
        ips |= set(w.ip_addresses or [])
    entity_ids = {x.id for x in clients + devices + hosts + workloads}

    timeline: list[dict[str, Any]] = []
    for e in await db.scalars(
        select(EventRecord)
        .where(EventRecord.entity_id.in_(entity_ids), EventRecord.occurred_at >= since)
        .order_by(EventRecord.occurred_at.desc())
        .limit(200)
    ):
        timeline.append(
            {"at": e.occurred_at, "kind": "event", "title": e.type, "detail": e.payload, "severity": e.severity, "source": e.source}
        )
    for s in await db.scalars(
        select(ClientSighting)
        .where(ClientSighting.client_id.in_([c.id for c in clients]), ClientSighting.last_seen_at >= since)
        .order_by(ClientSighting.first_seen_at.desc())
        .limit(100)
    ):
        timeline.append(
            {
                "at": s.first_seen_at,
                "kind": "sighting",
                "title": f"Seen at {s.ip_address or 'no address'} ({s.type.lower()})",
                "detail": {"until": s.last_seen_at, "uplink": s.uplink_device_external_id},
                "severity": "info",
                "source": "client-intel",
            }
        )
    if ips:
        conds = [LogEvent.source_ip.in_(ips)] + [LogEvent.message.like(f"%{ip}%") for ip in list(ips)[:5]]
        for lg in await db.scalars(
            select(LogEvent).where(or_(*conds), LogEvent.received_at >= since).order_by(LogEvent.received_at.desc()).limit(300)
        ):
            timeline.append(
                {
                    "at": lg.received_at,
                    "kind": "log",
                    "title": lg.message[:240],
                    "detail": {"host": lg.host, "app": lg.app, "category": lg.category, "source_ip": lg.source_ip},
                    "severity": SYSLOG_SEVERITY[lg.severity] if 0 <= lg.severity < 8 else "info",
                    "source": "logs",
                    "is_demo": lg.is_demo,
                }
            )
    for a in await db.scalars(select(Alert).where(Alert.entity_id.in_(entity_ids), Alert.pending_since >= since)):
        timeline.append(
            {
                "at": a.fired_at or a.pending_since,
                "kind": "alert",
                "title": f"Alert {a.state}: {a.label}",
                "detail": {"value": a.value, "threshold": a.threshold},
                "severity": a.severity,
                "source": "monitoring",
            }
        )
    for r in await db.scalars(select(AuditRecord).where(AuditRecord.target_id.in_(entity_ids)).order_by(AuditRecord.seq.desc()).limit(50)):
        timeline.append(
            {
                "at": r.occurred_at,
                "kind": "audit",
                "title": f"{r.actor_name}: {r.action}",
                "detail": r.detail,
                "severity": "info",
                "source": "audit",
            }
        )
    for c in await db.scalars(select(SocCase)):
        if any(str(e.get("id")) in entity_ids | ips for e in c.entities or []):
            timeline.append(
                {
                    "at": c.created_at,
                    "kind": "case",
                    "title": f"Case opened: {c.title} ({c.status.replace('_', ' ')})",
                    "detail": {"case_id": c.id},
                    "severity": c.severity,
                    "source": "soc",
                }
            )
    timeline.sort(key=lambda t: t["at"], reverse=True)
    return {
        "query": term,
        "kind": "ip" if is_ip else "mac" if mac else "name",
        "addresses": sorted(ips),
        "entities": (
            [
                {
                    "type": "client",
                    "id": c.id,
                    "name": c.alias or c.name,
                    "detail": c.ip_address,
                    "href": f"/inventory/clients/{c.id}",
                    "is_demo": c.is_demo,
                }
                for c in clients
            ]
            + [
                {
                    "type": "device",
                    "id": d.id,
                    "name": d.name,
                    "detail": f"{d.model} · {d.ip_address}",
                    "href": f"/inventory/devices/{d.id}",
                    "is_demo": d.is_demo,
                }
                for d in devices
            ]
            + [
                {"type": "host", "id": h.id, "name": h.name, "detail": h.kind, "href": f"/servers/hosts/{h.id}", "is_demo": h.is_demo}
                for h in hosts
            ]
            + [
                {
                    "type": "workload",
                    "id": w.id,
                    "name": w.name,
                    "detail": w.kind,
                    "href": f"/servers/hosts/{w.host_id}",
                    "is_demo": w.is_demo,
                }
                for w in workloads
            ]
        ),
        "timeline": timeline[:500],
        "days": days,
    }

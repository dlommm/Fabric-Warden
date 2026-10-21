"""Client Intelligence: one client's identity, attachment path and address history."""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from warden.api.deps import DB, Allowed, State, require_module
from warden.api.inventory import _event, _network_for, _subnets, provenance
from warden.models import Client, ClientSighting, Device, EventRecord, FirewallZone, IpPrefix, IpReservation, Network
from warden.security.rbac import P

router = APIRouter(prefix="/api/client-intel", tags=["client-intel"], dependencies=[Depends(require_module("client-intel"))])

Reader = Allowed(P.INVENTORY_READ)


def _uplink_path(start: str | None, site_id: str, devices: dict[tuple[str, str], Device]) -> list[dict[str, Any]]:
    """Walk uplinks from the client's attachment point to the top of the tree."""
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur = devices.get((site_id, start or ""))
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        path.append({"id": cur.id, "name": cur.name, "model": cur.model, "state": cur.state, "features": cur.features})
        cur = devices.get((site_id, cur.uplink_device_external_id or ""))
    return path


@router.get("/clients/{client_id}")
async def client_360(client_id: str, db: DB, state: State, principal: Reader) -> dict:
    c = await db.get(Client, client_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    devices = {(d.site_id, d.external_id): d for d in await db.scalars(select(Device).where(Device.site_id == c.site_id))}
    nets = _subnets(list(await db.scalars(select(Network).where(Network.present.is_(True)))))
    net = _network_for(c.ip_address, nets)
    zone = None
    if net and net.zone_external_id:
        z = await db.scalar(
            select(FirewallZone).where(FirewallZone.site_id == net.site_id, FirewallZone.external_id == net.zone_external_id)
        )
        zone = {"id": z.id, "name": z.name} if z else None

    sightings = list(
        await db.scalars(select(ClientSighting).where(ClientSighting.client_id == c.id).order_by(ClientSighting.first_seen_at.desc()))
    )
    addresses: dict[str, dict[str, Any]] = {}
    for s in sightings:
        if not s.ip_address:
            continue
        a = addresses.setdefault(
            s.ip_address, {"address": s.ip_address, "first_seen_at": s.first_seen_at, "last_seen_at": s.last_seen_at, "sightings": 0}
        )
        a["sightings"] += 1
        a["first_seen_at"] = min(a["first_seen_at"], s.first_seen_at)
        a["last_seen_at"] = max(a["last_seen_at"], s.last_seen_at)

    def _uplink_ref(ext: str | None) -> dict[str, Any] | None:
        d = devices.get((c.site_id, ext or ""))
        return {"id": d.id, "name": d.name} if d else None

    reservation = None
    if await state.modules.is_enabled(db, "ipam"):
        reservation = await _reservation_for(db, c)

    events = await db.scalars(select(EventRecord).where(EventRecord.entity_id == c.id).order_by(EventRecord.seq.desc()).limit(50))
    return {
        "id": c.id,
        "name": c.name,
        "alias": c.alias,
        "owner": c.owner,
        "notes": c.notes,
        "labels": c.labels,
        "type": c.type,
        "mac_address": c.mac_address,
        "ip_address": c.ip_address,
        "connected_at": c.connected_at.isoformat() if c.connected_at else None,
        "access_type": (c.access or {}).get("type"),
        "network": (
            {
                "id": net.id,
                "name": net.name,
                "vlan_id": net.vlan_id,
                "subnet": str(ipaddress.ip_network(f"{net.ipv4['hostIpAddress']}/{net.ipv4['prefixLength']}", strict=False)),
            }
            if net and net.ipv4
            else None
        ),
        "zone": zone,
        "path": _uplink_path(c.uplink_device_external_id, c.site_id, devices),
        "sightings": [
            {
                "id": s.id,
                "ip_address": s.ip_address,
                "uplink": _uplink_ref(s.uplink_device_external_id),
                "type": s.type,
                "first_seen_at": s.first_seen_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat(),
                "duration_seconds": int((s.last_seen_at - s.first_seen_at).total_seconds()),
            }
            for s in sightings[:200]
        ],
        "addresses": [
            a | {"first_seen_at": a["first_seen_at"].isoformat(), "last_seen_at": a["last_seen_at"].isoformat()}
            for a in sorted(addresses.values(), key=lambda a: a["last_seen_at"], reverse=True)
        ],
        "history_summary": {
            "sightings": len(sightings),
            "distinct_addresses": len(addresses),
            "distinct_uplinks": len({s.uplink_device_external_id for s in sightings if s.uplink_device_external_id}),
            "tracking_since": sightings[-1].first_seen_at.isoformat() if sightings else None,
        },
        "reservation": reservation,
        "events": [_event(e) for e in events],
        "provenance": provenance(c),
        "fingerprint": None,  # not exposed by the official API; see the module warning
    }


async def _reservation_for(db: DB, c: Client) -> dict[str, Any] | None:
    """The documented reservation for this client, by MAC first, then by current address."""
    rows = list(await db.scalars(select(IpReservation).where((IpReservation.mac_address == c.mac_address) if c.mac_address else False)))
    if not rows and c.ip_address:
        rows = list(await db.scalars(select(IpReservation).where(IpReservation.address == c.ip_address)))
    if not rows:
        return None
    r = rows[0]
    prefix = await db.get(IpPrefix, r.prefix_id)
    matches_mac = r.mac_address is None or r.mac_address == c.mac_address
    at_address = c.ip_address is not None and ipaddress.ip_address(c.ip_address) == ipaddress.ip_address(r.address)
    return {
        "id": r.id,
        "address": r.address,
        "hostname": r.hostname,
        "kind": r.kind,
        "prefix": {"id": prefix.id, "cidr": prefix.cidr} if prefix else None,
        "status": "ok" if matches_mac and at_address else "conflict" if not matches_mac else "elsewhere",
    }

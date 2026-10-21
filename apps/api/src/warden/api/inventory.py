"""Inventory read API plus user-owned metadata (labels, notes)."""

from __future__ import annotations

import ipaddress
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.db import utcnow
from warden.models import (
    Client,
    Controller,
    Device,
    EventRecord,
    FirewallPolicy,
    FirewallZone,
    Network,
    Port,
    Radio,
    Site,
    Wan,
)
from warden.security.rbac import P

router = APIRouter(prefix="/api/inventory", tags=["inventory"], dependencies=[Depends(require_module("inventory"))])

Reader = Allowed(P.INVENTORY_READ)


def provenance(row: Any) -> dict[str, Any]:
    age = (utcnow() - row.observed_at).total_seconds()
    return {
        "source": row.source,
        "is_demo": row.is_demo,
        "observed_at": row.observed_at.isoformat(),
        "first_seen_at": row.first_seen_at.isoformat(),
        "age_seconds": int(age),
        "present": row.present,
    }


def device_summary(d: Device) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "model": d.model,
        "mac_address": d.mac_address,
        "ip_address": d.ip_address,
        "firmware_version": d.firmware_version,
        "firmware_updatable": d.firmware_updatable,
        "state": d.state,
        "features": d.features,
        "labels": d.labels,
        "site_id": d.site_id,
        "cpu_pct": (d.stats or {}).get("cpuUtilizationPct"),
        "memory_pct": (d.stats or {}).get("memoryUtilizationPct"),
        "uptime_sec": (d.stats or {}).get("uptimeSec"),
        "provenance": provenance(d),
    }


def _search(q: Select, cols: list[Any], term: str | None) -> Select:
    if term:
        like = f"%{term.strip().lower()}%"
        q = q.where(or_(*[func.lower(c).like(like) for c in cols]))
    return q


@router.get("/summary")
async def summary(db: DB, principal: Reader) -> dict:
    devices = list(await db.scalars(select(Device).where(Device.present.is_(True))))
    clients = list(await db.scalars(select(Client).where(Client.present.is_(True))))
    controllers = list(await db.scalars(select(Controller)))
    by_state: dict[str, int] = {}
    by_role = {"gateway": 0, "switching": 0, "accessPoint": 0}
    for d in devices:
        by_state[d.state] = by_state.get(d.state, 0) + 1
        role = "gateway" if "gateway" in d.features else "accessPoint" if "accessPoint" in d.features else "switching"
        by_role[role] += 1
    by_type: dict[str, int] = {}
    for c in clients:
        by_type[c.type] = by_type.get(c.type, 0) + 1
    since = utcnow() - timedelta(hours=24)
    recent = await db.scalars(
        select(EventRecord)
        .where(EventRecord.type.like("inventory.%") | EventRecord.type.like("controller.status%"))
        .order_by(EventRecord.seq.desc())
        .limit(12)
    )
    attention = [device_summary(d) for d in devices if d.state != "ONLINE" or d.firmware_updatable]
    return {
        "devices": {"total": len(devices), "by_state": by_state, "by_role": by_role},
        "clients": {"total": len(clients), "by_type": by_type},
        "networks": await db.scalar(select(func.count()).select_from(Network).where(Network.present.is_(True))) or 0,
        "wans": await db.scalar(select(func.count()).select_from(Wan).where(Wan.present.is_(True))) or 0,
        "controllers": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "status_detail": c.status_detail,
                "is_demo": c.is_demo,
                "application_version": c.application_version,
                "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            }
            for c in controllers
        ],
        "attention": attention,
        "events_24h": await db.scalar(select(func.count()).select_from(EventRecord).where(EventRecord.occurred_at >= since)) or 0,
        "recent_events": [_event(e) for e in recent],
        "demo_only": bool(controllers) and all(c.is_demo for c in controllers),
    }


def _event(e: EventRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type,
        "occurred_at": e.occurred_at.isoformat(),
        "source": e.source,
        "severity": e.severity,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "payload": e.payload,
    }


@router.get("/devices")
async def list_devices(
    db: DB,
    principal: Reader,
    q: str | None = None,
    state: str | None = None,
    role: Annotated[str | None, Query(pattern="^(gateway|switching|accessPoint)$")] = None,
    include_missing: bool = False,
) -> dict:
    stmt = select(Device).order_by(Device.name)
    if not include_missing:
        stmt = stmt.where(Device.present.is_(True))
    if state:
        stmt = stmt.where(Device.state == state)
    stmt = _search(stmt, [Device.name, Device.model, Device.mac_address, Device.ip_address], q)
    rows = [d for d in await db.scalars(stmt) if not role or role in d.features]
    ids = {(d.site_id, d.external_id): d.id for d in await db.scalars(select(Device))}
    items = [device_summary(d) | {"uplink_id": ids.get((d.site_id, d.uplink_device_external_id or ""))} for d in rows]
    return {"items": items, "total": len(rows)}


@router.get("/devices/{device_id}")
async def get_device(device_id: str, db: DB, principal: Reader) -> dict:
    d = await db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    ports = await db.scalars(select(Port).where(Port.device_id == d.id).order_by(Port.idx))
    radios = await db.scalars(select(Radio).where(Radio.device_id == d.id).order_by(Radio.frequency_ghz))
    uplink = None
    if d.uplink_device_external_id:
        u = await db.scalar(select(Device).where(Device.site_id == d.site_id, Device.external_id == d.uplink_device_external_id))
        uplink = {"id": u.id, "name": u.name, "model": u.model} if u else None
    downstream = await db.scalars(select(Device).where(Device.site_id == d.site_id, Device.uplink_device_external_id == d.external_id))
    clients = await db.scalars(
        select(Client)
        .where(Client.site_id == d.site_id, Client.uplink_device_external_id == d.external_id, Client.present.is_(True))
        .order_by(Client.name)
    )
    history = await db.scalars(select(EventRecord).where(EventRecord.entity_id == d.id).order_by(EventRecord.seq.desc()).limit(50))
    radio_stats = {r["frequencyGHz"]: r for r in ((d.stats or {}).get("interfaces") or {}).get("radios", [])}
    return {
        **device_summary(d),
        "notes": d.notes,
        "stats": d.stats,
        "stats_observed_at": d.stats_observed_at.isoformat() if d.stats_observed_at else None,
        "ports": [
            {
                "idx": p.idx,
                "connector": p.connector,
                "state": p.state,
                "speed_mbps": p.speed_mbps,
                "max_speed_mbps": p.max_speed_mbps,
                "poe": p.poe,
            }
            for p in ports
        ],
        "radios": [
            {
                "frequency_ghz": r.frequency_ghz,
                "channel": r.channel,
                "channel_width_mhz": r.channel_width_mhz,
                "wlan_standard": r.wlan_standard,
                "tx_retries_pct": radio_stats.get(r.frequency_ghz, {}).get("txRetriesPct"),
            }
            for r in radios
        ],
        "uplink": uplink,
        "downstream": [{"id": x.id, "name": x.name, "model": x.model, "state": x.state} for x in downstream],
        "clients": [{"id": c.id, "name": c.name, "ip_address": c.ip_address, "type": c.type} for c in clients],
        "history": [_event(e) for e in history],
    }


class MetadataPatch(BaseModel):
    labels: list[Annotated[str, Field(min_length=1, max_length=48)]] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=20000)


@router.patch("/devices/{device_id}")
async def update_device_metadata(device_id: str, body: MetadataPatch, db: DB, principal: Allowed(P.INVENTORY_EDIT)) -> dict:
    d = await db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    if body.labels is not None:
        d.labels = sorted(set(body.labels))
    if body.notes is not None:
        d.notes = body.notes
    await audit.record(
        db,
        principal.actor,
        "inventory.device.metadata",
        target_type="device",
        target_id=d.id,
        detail=body.model_dump(exclude_unset=True, exclude={"notes"}) | ({"notes": "updated"} if body.notes is not None else {}),
    )
    return device_summary(d)


def _network_for(ip: str | None, nets: list[tuple[ipaddress.IPv4Network, Network]]) -> Network | None:
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for net, row in nets:
        if addr in net:
            return row
    return None


def _subnets(networks: list[Network]) -> list[tuple[ipaddress.IPv4Network, Network]]:
    out = []
    for n in networks:
        cfg = n.ipv4 or {}
        if cfg.get("hostIpAddress") and cfg.get("prefixLength"):
            out.append((ipaddress.ip_network(f"{cfg['hostIpAddress']}/{cfg['prefixLength']}", strict=False), n))
    return out


@router.get("/clients")
async def list_clients(db: DB, principal: Reader, q: str | None = None, type: str | None = None, network_id: str | None = None) -> dict:
    stmt = select(Client).where(Client.present.is_(True)).order_by(Client.name)
    if type:
        stmt = stmt.where(Client.type == type)
    stmt = _search(stmt, [Client.name, Client.mac_address, Client.ip_address], q)
    nets = _subnets(list(await db.scalars(select(Network).where(Network.present.is_(True)))))
    devices = {(d.site_id, d.external_id): d for d in await db.scalars(select(Device))}
    items = []
    for c in await db.scalars(stmt):
        net = _network_for(c.ip_address, nets)
        if network_id and (net is None or net.id != network_id):
            continue
        up = devices.get((c.site_id, c.uplink_device_external_id or ""))
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "type": c.type,
                "mac_address": c.mac_address,
                "ip_address": c.ip_address,
                "connected_at": c.connected_at.isoformat() if c.connected_at else None,
                "access_type": (c.access or {}).get("type"),
                "network": {"id": net.id, "name": net.name, "vlan_id": net.vlan_id} if net else None,
                "uplink": {"id": up.id, "name": up.name} if up else None,
                "provenance": provenance(c),
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/networks")
async def list_networks(db: DB, principal: Reader) -> dict:
    networks = list(await db.scalars(select(Network).where(Network.present.is_(True)).order_by(Network.vlan_id)))
    zones = {(z.site_id, z.external_id): z for z in await db.scalars(select(FirewallZone))}
    nets = _subnets(networks)
    counts: dict[str, int] = {}
    for c in await db.scalars(select(Client).where(Client.present.is_(True))):
        if n := _network_for(c.ip_address, nets):
            counts[n.id] = counts.get(n.id, 0) + 1
    items = []
    for n in networks:
        cfg = n.ipv4 or {}
        dhcp = cfg.get("dhcpConfiguration") or {}
        rng = dhcp.get("ipAddressRange") or {}
        pool = None
        if rng.get("start") and rng.get("stop"):
            pool = int(ipaddress.ip_address(rng["stop"])) - int(ipaddress.ip_address(rng["start"])) + 1
        zone = zones.get((n.site_id, n.zone_external_id or ""))
        items.append(
            {
                "id": n.id,
                "name": n.name,
                "vlan_id": n.vlan_id,
                "management": n.management,
                "enabled": n.enabled,
                "is_default": n.is_default,
                "origin": n.origin,
                "subnet": f"{cfg['hostIpAddress']}/{cfg['prefixLength']}" if cfg.get("hostIpAddress") else None,
                "gateway": cfg.get("hostIpAddress"),
                "dhcp_mode": dhcp.get("mode"),
                "dhcp_range": rng or None,
                "dhcp_pool_size": pool,
                "active_clients": counts.get(n.id, 0),
                "zone": {"id": zone.id, "name": zone.name} if zone else None,
                "provenance": provenance(n),
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/wans")
async def list_wans(db: DB, principal: Reader) -> dict:
    rows = await db.scalars(select(Wan).where(Wan.present.is_(True)).order_by(Wan.name))
    return {"items": [{"id": w.id, "name": w.name, "provenance": provenance(w)} for w in rows]}


@router.get("/firewall")
async def firewall_overview(db: DB, principal: Reader) -> dict:
    """Read-only zone/policy listing. Analysis arrives with the firewall module."""
    zones = list(await db.scalars(select(FirewallZone).where(FirewallZone.present.is_(True)).order_by(FirewallZone.name)))
    zname = {(z.site_id, z.external_id): z.name for z in zones}
    policies = await db.scalars(select(FirewallPolicy).where(FirewallPolicy.present.is_(True)).order_by(FirewallPolicy.index))
    return {
        "zones": [{"id": z.id, "name": z.name, "origin": z.origin, "network_count": len(z.network_external_ids)} for z in zones],
        "policies": [
            {
                "id": p.id,
                "name": p.name,
                "enabled": p.enabled,
                "action": p.action,
                "index": p.index,
                "origin": p.origin,
                "logging_enabled": p.logging_enabled,
                "source_zone": zname.get((p.site_id, p.source_zone_external_id or "")),
                "destination_zone": zname.get((p.site_id, p.destination_zone_external_id or "")),
            }
            for p in policies
        ],
    }


@router.get("/sites")
async def list_sites(db: DB, principal: Reader) -> dict:
    rows = await db.scalars(select(Site).order_by(Site.name))
    return {"items": [{"id": s.id, "name": s.name, "controller_id": s.controller_id, "provenance": provenance(s)} for s in rows]}


@router.get("/search")
async def search(db: DB, principal: Reader, q: Annotated[str, Query(min_length=1, max_length=128)]) -> dict:
    like = f"%{q.strip().lower()}%"
    devices = await db.scalars(
        select(Device)
        .where(
            Device.present.is_(True),
            or_(
                func.lower(Device.name).like(like),
                Device.mac_address.like(like),
                Device.ip_address.like(like),
                func.lower(Device.model).like(like),
            ),
        )
        .limit(8)
    )
    clients = await db.scalars(
        select(Client)
        .where(
            Client.present.is_(True), or_(func.lower(Client.name).like(like), Client.mac_address.like(like), Client.ip_address.like(like))
        )
        .limit(8)
    )
    networks = await db.scalars(select(Network).where(Network.present.is_(True), func.lower(Network.name).like(like)).limit(5))
    results: list[dict[str, Any]] = []
    results += [
        {
            "kind": "device",
            "id": d.id,
            "title": d.name,
            "subtitle": f"{d.model} · {d.ip_address or d.mac_address}",
            "href": f"/inventory/devices/{d.id}",
        }
        for d in devices
    ]
    results += [
        {
            "kind": "client",
            "id": c.id,
            "title": c.name,
            "subtitle": c.ip_address or c.mac_address or c.type,
            "href": f"/inventory/clients?q={c.name}",
        }
        for c in clients
    ]
    results += [
        {
            "kind": "network",
            "id": n.id,
            "title": n.name,
            "subtitle": f"VLAN {n.vlan_id}" if n.vlan_id else n.management,
            "href": "/inventory/networks",
        }
        for n in networks
    ]
    return {"results": results}

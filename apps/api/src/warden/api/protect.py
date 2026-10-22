"""UniFi Protect: connections, cameras and NVRs, correlated with the network inventory. Read-only.

A camera is joined to the network by its MAC address (the Protect API
publishes no camera IP): the matching UniFi client gives the uplink device.
The switch port is only known when a documented cable (Cabling module) runs
from a port on that device to an item or external point named after the
camera; the official Network API reports no port for a client.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.api.deps import DB, Allowed, require_module
from warden.api.integration_connections import add_connection_routes
from warden.db import utcnow
from warden.integrations.hosts.sync import ClientIndex
from warden.integrations.protect.simulator import SIM_PROTECT_KEY
from warden.models import Cable, CableEnd, Client, Device, IntegrationConnection, Port, ProtectDevice, RackItem
from warden.security.rbac import P

router = APIRouter(prefix="/api/protect", tags=["protect"], dependencies=[Depends(require_module("unifi-protect"))])
Reader = Allowed(P.INVENTORY_READ)
STALE = timedelta(minutes=10)

add_connection_routes(router, "unifi-protect", "protect", demo=("unvr-protect (demo)", "https://protect-simulator.local", SIM_PROTECT_KEY))


def _prov(d: ProtectDevice) -> dict[str, Any]:
    return {
        "source": d.source,
        "observed_at": d.observed_at,
        "first_seen_at": d.first_seen_at,
        "present": d.present,
        "is_demo": d.is_demo,
        "age_seconds": int((utcnow() - d.observed_at).total_seconds()),
    }


async def _documented_ports(db: AsyncSession) -> list[tuple[str, int, str, str]]:
    """(device_id, port_idx, far-end text, cable label) for every cable with an observed device port at one end."""
    ends = list(await db.scalars(select(CableEnd)))
    items = {i.id: i.name for i in await db.scalars(select(RackItem))}
    labels = {c.id: c.label for c in await db.scalars(select(Cable).where(Cable.status != "decommissioned"))}
    by_cable: dict[str, dict[str, CableEnd]] = {}
    for e in ends:
        by_cable.setdefault(e.cable_id, {})[e.end] = e
    out = []
    for cid, pair in by_cable.items():
        if cid not in labels or len(pair) != 2:
            continue
        for near, far in ((pair["a"], pair["b"]), (pair["b"], pair["a"])):
            if near.kind == "device_port" and near.device_id and near.port_idx is not None:
                text = far.text if far.kind == "external" else items.get(far.item_id or "", "") if far.kind == "item" else ""
                if text:
                    out.append((near.device_id, near.port_idx, text.strip().lower(), labels[cid]))
    return out


async def correlate(db: AsyncSession, rows: list[ProtectDevice]) -> list[dict[str, Any]]:
    clients = list(await db.scalars(select(Client).where(Client.present.is_(True))))
    index = ClientIndex(clients)
    devices = list(await db.scalars(select(Device).where(Device.present.is_(True))))
    dev_by_ext = {(d.site_id, d.external_id): d for d in devices}
    ports = {(p.device_id, p.idx): p for p in await db.scalars(select(Port))}
    cabled = await _documented_ports(db)
    conns = {c.id: c for c in await db.scalars(select(IntegrationConnection))}
    out = []
    for d in rows:
        client, reason = index.match([], [d.mac_address] if d.mac_address else [], [d.name])
        uplink = dev_by_ext.get((client.site_id, client.uplink_device_external_id or "")) if client else None
        link = None
        port_out = None
        port_note = None
        if client is None:
            port_note = "No network client has this MAC address, so the uplink is unknown."
        elif uplink is None:
            port_note = "The network reports no uplink device for this client."
        elif "accessPoint" in (uplink.features or []) and client.type == "WIRELESS":
            link = "wifi"
            port_note = "Connected over Wi-Fi; no switch port or PoE."
        else:
            link = "wired"
            keys = {d.name.strip().lower(), (d.mac_address or "").lower()} - {""}
            hit = next(((dev, idx, label) for dev, idx, text, label in cabled if dev == uplink.id and text in keys), None)
            if hit:
                p = ports.get((uplink.id, hit[1]))
                port_out = {
                    "idx": hit[1],
                    "state": p.state if p else None,
                    "speed_mbps": p.speed_mbps if p else None,
                    "poe": p.poe if p else None,
                    "basis": "documented",
                    "cable": hit[2],
                }
            else:
                port_note = (
                    "The official Network API does not report which switch port a client uses. "
                    "Document the cable in Cabling to pin the port."
                )
        poe_up = None
        if uplink is not None and link == "wired":
            poe_up = sum(1 for (did, _), p in ports.items() if did == uplink.id and p.poe and str(p.poe.get("state")).upper() == "UP")
        c = conns.get(d.connection_id)
        stale = d.observed_at < utcnow() - STALE
        out.append(
            {
                "id": d.id,
                "kind": d.kind,
                "name": d.name,
                "type": d.type,
                "model_key": d.model_key,
                "mac_address": d.mac_address,
                "state": "UNKNOWN" if (stale or not d.present) and d.kind == "camera" else d.state,
                "extra": d.extra or {},
                "connection": {"id": c.id, "name": c.name, "status": c.status} if c else None,
                "client": (
                    {
                        "id": client.id,
                        "name": client.alias or client.name,
                        "ip_address": client.ip_address,
                        "type": client.type,
                        "match": reason,
                    }
                    if client
                    else None
                ),
                "uplink": (
                    {"id": uplink.id, "name": uplink.name, "model": uplink.model, "state": uplink.state, "poe_ports_up": poe_up}
                    if uplink
                    else None
                ),
                "link": link,
                "port": port_out,
                "port_note": port_note,
                "provenance": _prov(d),
                "is_demo": d.is_demo,
            }
        )
    return out


async def _rows(db: AsyncSession, kind: str) -> list[ProtectDevice]:
    q = select(ProtectDevice).where(ProtectDevice.kind == kind, ProtectDevice.present.is_(True)).order_by(ProtectDevice.name)
    return list(await db.scalars(q))


@router.get("/cameras")
async def list_cameras(db: DB, principal: Reader) -> dict:
    items = await correlate(db, await _rows(db, "camera"))
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "connected": sum(i["state"] == "CONNECTED" for i in items),
            "disconnected": sum(i["state"] != "CONNECTED" for i in items),
            "on_network": sum(i["client"] is not None for i in items),
        },
        "notes": [
            "Cameras are matched to network clients by MAC address; the Protect API publishes no camera IP address.",
            "Switch ports come only from documented cables; the official Network API does not report a client's port.",
        ],
    }


@router.get("/nvrs")
async def list_nvrs(db: DB, principal: Reader) -> dict:
    return {"items": await correlate(db, await _rows(db, "nvr"))}


async def protect_findings(db: AsyncSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in await db.scalars(select(IntegrationConnection).where(IntegrationConnection.kind == "unifi-protect")):
        if c.status in ("unreachable", "degraded"):
            out.append(
                {"kind": "connection", "severity": "critical" if c.status == "unreachable" else "warning", "title": f"{c.name}: {c.status}",
                 "detail": c.status_detail, "target": {"type": "connection", "id": c.id, "name": c.name}, "is_demo": c.is_demo}
            )  # fmt: skip
    cams = await correlate(db, await _rows(db, "camera"))
    nvrs = await correlate(db, await _rows(db, "nvr"))
    for cam in cams:
        tgt = {"type": "camera", "id": cam["id"], "name": cam["name"]}
        if cam["state"] != "CONNECTED":
            poe = ((cam["port"] or {}).get("poe") or {}) if cam["port"] else {}
            detail = f"Protect reports {cam['state'].lower()}."
            if poe and str(poe.get("state")).upper() != "UP":
                detail += f" PoE is off on the documented port {cam['uplink']['name']} #{cam['port']['idx']}."
            elif cam["client"] is None:
                detail += " The network does not see it either."
            elif cam["client"]:
                detail += f" The network still reports it as a client ({cam['client']['ip_address'] or 'no address'})."
            out.append(
                {"kind": "camera_offline", "severity": "critical" if cam["state"] == "DISCONNECTED" else "warning",
                 "title": f"{cam['name']} is {cam['state'].lower()}", "detail": detail, "target": tgt, "is_demo": cam["is_demo"]}
            )  # fmt: skip
        if cam["client"] is None:
            out.append(
                {"kind": "not_on_network", "severity": "info", "title": f"{cam['name']} is not in the network inventory",
                 "detail": "No UniFi client has its MAC address: another network, a different controller, or not seen yet.",
                 "target": tgt, "is_demo": cam["is_demo"]}
            )  # fmt: skip
    for n in nvrs:
        status = (n["extra"] or {}).get("arm_status")
        if status == "breach":
            out.append(
                {"kind": "alarm_breach", "severity": "critical", "title": f"{n['name']}: alarm breach",
                 "detail": f"{(n['extra'] or {}).get('breach_event_count') or 0} breach events.",
                 "target": {"type": "nvr", "id": n["id"], "name": n["name"]}, "is_demo": n["is_demo"]}
            )  # fmt: skip
    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (order.get(f["severity"], 3), f["title"]))
    return out


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    items = await protect_findings(db)
    return {"items": items, "counts": {s: sum(f["severity"] == s for f in items) for s in ("critical", "warning", "info")}}

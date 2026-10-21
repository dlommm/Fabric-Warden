"""Network topology: physical uplink tree and logical network view, with VLAN overlay.

The physical graph comes from each device's and client's reported uplink. The
official API does not say which *port* carries an uplink, so edges have no port
numbers unless a documented cable path connects the two devices. A VLAN overlay
marks the devices that carry clients of that network on their path to the
gateway; it is inferred from client attachment, not from switch port VLAN
configuration (which the official API does not expose).
"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from warden.api.deps import DB, Allowed, State, require_module
from warden.api.inventory import _subnets
from warden.models import Alert, Client, Device, Network, Rack, RackItem
from warden.security.rbac import P

router = APIRouter(prefix="/api/topology", tags=["topology"], dependencies=[Depends(require_module("topology"))])

Reader = Allowed(P.INVENTORY_READ)


def _role(d: Device) -> str:
    f = set(d.features or [])
    if "gateway" in f:
        return "gateway"
    if "switching" in f:
        return "switch"
    if "accessPoint" in f:
        return "ap"
    return "device"


def _health(d: Device, alerts: int) -> str:
    if d.state != "ONLINE":
        return "down" if d.state in ("OFFLINE", "DISCONNECTED") else "degraded"
    return "alerting" if alerts else "ok"


@router.get("")
async def topology(
    db: DB,
    state: State,
    principal: Reader,
    layer: Literal["physical", "logical"] = "physical",
    vlan: int | None = Query(default=None, ge=1, le=4094),
    clients: bool = False,
) -> dict:
    devices = list(await db.scalars(select(Device).where(Device.present.is_(True))))
    all_clients = list(await db.scalars(select(Client).where(Client.present.is_(True))))
    networks = list(await db.scalars(select(Network).where(Network.present.is_(True))))
    subnets = _subnets(networks)
    by_ext = {d.external_id: d for d in devices}

    alerts: dict[str, int] = {}
    if await state.modules.is_enabled(db, "monitoring"):
        for a in await db.scalars(select(Alert).where(Alert.state == "firing", Alert.entity_type == "device")):
            alerts[a.entity_id] = alerts.get(a.entity_id, 0) + 1

    placement: dict[str, str] = {}
    if await state.modules.is_enabled(db, "dcim"):
        racks = {r.id: r.name for r in await db.scalars(select(Rack))}
        for i in await db.scalars(select(RackItem).where(RackItem.device_id.is_not(None))):
            placement[i.device_id or ""] = f"{racks.get(i.rack_id, '?')} U{i.position}" if i.position else racks.get(i.rack_id, "")

    def net_of(ip: str | None) -> Network | None:
        try:
            addr = ipaddress.ip_address(ip or "")
        except ValueError:
            return None
        return next((row for net, row in subnets if addr in net), None)

    # Depth = hops from the root (a device with no known uplink).
    def depth(d: Device, seen: frozenset[str] = frozenset()) -> int:
        up = by_ext.get(d.uplink_device_external_id or "")
        if up is None or d.id in seen:
            return 0
        return 1 + depth(up, seen | {d.id})

    # VLAN overlay: devices on the attachment path of the VLAN's clients.
    carriers: dict[str, int] = {}
    if vlan is not None:
        for c in all_clients:
            n = net_of(c.ip_address)
            if not n or n.vlan_id != vlan:
                continue
            cur = by_ext.get(c.uplink_device_external_id or "")
            seen: set[str] = set()
            while cur is not None and cur.id not in seen:
                seen.add(cur.id)
                carriers[cur.id] = carriers.get(cur.id, 0) + 1
                cur = by_ext.get(cur.uplink_device_external_id or "")

    attached: dict[str, int] = {}
    for c in all_clients:
        if c.uplink_device_external_id:
            attached[c.uplink_device_external_id] = attached.get(c.uplink_device_external_id, 0) + 1

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for d in devices:
        up = (d.stats or {}).get("uplink") or {}
        nodes.append(
            {
                "id": d.id,
                "type": "device",
                "role": _role(d),
                "name": d.name,
                "model": d.model,
                "ip_address": d.ip_address,
                "state": d.state,
                "health": _health(d, alerts.get(d.id, 0)),
                "alerts": alerts.get(d.id, 0),
                "depth": depth(d),
                "clients": attached.get(d.external_id, 0),
                "placement": placement.get(d.id),
                "vlan_clients": carriers.get(d.id, 0) if vlan is not None else None,
                "is_demo": d.is_demo,
            }
        )
        parent = by_ext.get(d.uplink_device_external_id or "")
        if layer == "physical" and parent is not None:
            edges.append(
                {
                    "id": f"{parent.id}>{d.id}",
                    "source": parent.id,
                    "target": d.id,
                    "kind": "uplink",
                    "tx_bps": up.get("txRateBps"),
                    "rx_bps": up.get("rxRateBps"),
                    "in_vlan": vlan is not None and d.id in carriers,
                }
            )

    if layer == "physical" and clients:
        for c in all_clients:
            parent = by_ext.get(c.uplink_device_external_id or "")
            n = net_of(c.ip_address)
            if parent is None:
                continue
            nodes.append(
                {
                    "id": c.id,
                    "type": "client",
                    "role": c.type.lower(),
                    "name": c.alias or c.name,
                    "ip_address": c.ip_address,
                    "network": n.name if n else None,
                    "vlan_id": n.vlan_id if n else None,
                    "depth": depth(parent) + 1,
                    "health": "ok",
                    "is_demo": c.is_demo,
                }
            )
            edges.append(
                {
                    "id": f"{parent.id}>{c.id}",
                    "source": parent.id,
                    "target": c.id,
                    "kind": "client",
                    "in_vlan": vlan is not None and n is not None and n.vlan_id == vlan,
                }
            )

    if layer == "logical":
        gateway = next((d for d in devices if _role(d) == "gateway"), None)
        per_net: dict[str, int] = {}
        for c in all_clients:
            n = net_of(c.ip_address)
            if n:
                per_net[n.id] = per_net.get(n.id, 0) + 1
        for n in networks:
            cidr = next((str(net) for net, row in subnets if row.id == n.id), None)
            nodes.append(
                {
                    "id": n.id,
                    "type": "network",
                    "role": n.management.lower(),
                    "name": n.name,
                    "vlan_id": n.vlan_id,
                    "cidr": cidr,
                    "clients": per_net.get(n.id, 0),
                    "enabled": n.enabled,
                    "depth": 1,
                    "health": "ok" if n.enabled else "disabled",
                    "highlight": vlan is not None and n.vlan_id == vlan,
                    "is_demo": n.is_demo,
                }
            )
            if gateway is not None and n.management == "GATEWAY":
                edges.append(
                    {
                        "id": f"{gateway.id}>{n.id}",
                        "source": gateway.id,
                        "target": n.id,
                        "kind": "routes",
                        "in_vlan": vlan is not None and n.vlan_id == vlan,
                    }
                )
        # Logical view keeps only the gateway among devices.
        nodes = [x for x in nodes if x["type"] != "device" or x["role"] == "gateway"]
        for x in nodes:
            if x["type"] == "device":
                x["depth"] = 0

    vlans = sorted({n.vlan_id for n in networks if n.vlan_id})
    return {
        "layer": layer,
        "vlan": vlan,
        "nodes": nodes,
        "edges": edges,
        "vlans": [{"vlan_id": v, "name": next((n.name for n in networks if n.vlan_id == v), "")} for v in vlans],
        "notes": [
            "Uplinks are reported per device; the official API does not identify the uplink port.",
            "VLAN overlay is inferred from where that network's clients attach, not from switch port VLAN configuration.",
        ],
    }

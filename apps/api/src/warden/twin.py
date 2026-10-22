"""Network Digital Twin: impact preview of a proposed change, from the observed inventory graph.

The graph is what the last inventory sync recorded: devices and their uplinks
(the physical tree), clients and the device they attach through, networks and
their subnets, firewall zones and the policies between them. Nothing here
reads the controller or changes anything; it answers "what would this touch?"
before someone approves.

What the inventory cannot know is said, not guessed: the official API does not
report which device is on a switch port, so a PoE cycle lists the devices
behind that switch only as *possibly* affected.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.models import ChangeRequest, Client, Device, FirewallPolicy, FirewallZone, Network

CLIENT_SAMPLE = 25


@dataclass
class Graph:
    devices: dict[str, Device]  # by external id
    children: dict[str, list[Device]]
    clients: list[Client]
    networks: list[Network]
    zones: dict[str, FirewallZone]  # by external id
    policies: list[FirewallPolicy]
    observed_at: Any = None

    def subtree(self, ext_id: str) -> list[tuple[Device, int, str]]:
        """Devices reaching the network through `ext_id`, with depth and the device they uplink to."""
        out: list[tuple[Device, int, str]] = []
        queue = [(ext_id, 1)]
        seen = {ext_id}
        while queue:
            parent, depth = queue.pop(0)
            for d in self.children.get(parent, []):
                if d.external_id in seen:
                    continue
                seen.add(d.external_id)
                out.append((d, depth, self.devices[parent].name if parent in self.devices else ""))
                queue.append((d.external_id, depth + 1))
        return out

    def network_of(self, ip: str | None) -> Network | None:
        if not ip:
            return None
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for n in self.networks:
            cfg = n.ipv4 or {}
            try:
                if addr in ipaddress.ip_network(f"{cfg.get('hostIpAddress')}/{cfg.get('prefixLength')}", strict=False):
                    return n
            except ValueError:
                continue
        return None


async def load(db: AsyncSession, site_id: str) -> Graph:
    devices = list(await db.scalars(select(Device).where(Device.site_id == site_id, Device.present.is_(True))))
    children: dict[str, list[Device]] = defaultdict(list)
    for d in devices:
        if d.uplink_device_external_id:
            children[d.uplink_device_external_id].append(d)
    return Graph(
        devices={d.external_id: d for d in devices},
        children=children,
        clients=list(await db.scalars(select(Client).where(Client.site_id == site_id, Client.present.is_(True)))),
        networks=list(await db.scalars(select(Network).where(Network.site_id == site_id, Network.present.is_(True)))),
        zones={z.external_id: z for z in await db.scalars(select(FirewallZone).where(FirewallZone.site_id == site_id))},
        policies=list(await db.scalars(select(FirewallPolicy).where(FirewallPolicy.site_id == site_id, FirewallPolicy.present.is_(True)))),
        observed_at=await db.scalar(select(func.max(Device.observed_at)).where(Device.site_id == site_id)),
    )


@dataclass
class Impact:
    summary: list[str] = field(default_factory=list)
    traffic_change: bool = True
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    networks: dict[str, dict[str, Any]] = field(default_factory=dict)
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    observed_at: Any = None

    def device(self, d: Device, relation: str, depth: int = 0, via: str = "") -> None:
        if d.id in self.devices and self.devices[d.id]["relation"] != "possibly":
            return
        self.devices[d.id] = {
            "id": d.id,
            "name": d.name,
            "model": d.model,
            "state": d.state,
            "relation": relation,
            "depth": depth,
            "via": via,
        }

    def client(self, c: Client, via: str, certain: bool = True) -> None:
        if c.id in self.clients and self.clients[c.id]["certain"]:
            return
        self.clients[c.id] = {
            "id": c.id,
            "name": c.alias or c.name,
            "ip": c.ip_address,
            "type": c.type,
            "via": via,
            "certain": certain,
        }

    def network(self, n: Network, relation: str, clients: int | None = None) -> None:
        self.networks.setdefault(n.id, {"id": n.id, "name": n.name, "vlan_id": n.vlan_id, "relation": relation, "clients": clients})

    def zone(self, z: FirewallZone | None, relation: str) -> None:
        if z is not None:
            self.zones.setdefault(z.id, {"id": z.id, "name": z.name, "relation": relation})

    def policy(self, p: FirewallPolicy, relation: str) -> None:
        self.policies.setdefault(p.id, {"id": p.id, "name": p.name, "action": p.action, "enabled": p.enabled, "relation": relation})

    def as_dict(self) -> dict[str, Any]:
        clients = sorted(self.clients.values(), key=lambda c: (not c["certain"], c["name"].lower()))
        return {
            "summary": self.summary,
            "traffic_change": self.traffic_change,
            "devices": sorted(self.devices.values(), key=lambda d: (d["relation"] != "target", d["depth"], d["name"].lower())),
            "clients": {
                "count": sum(1 for c in clients if c["certain"]),
                "possible": sum(1 for c in clients if not c["certain"]),
                "sample": clients[:CLIENT_SAMPLE],
            },
            "networks": list(self.networks.values()),
            "zones": list(self.zones.values()),
            "policies": list(self.policies.values()),
            "notes": self.notes,
            "basis": {
                "observed_at": self.observed_at,
                "text": "Computed from the inventory as last synced from the controller, not from a live read.",
            },
        }


def _clients_behind(g: Graph, imp: Impact, ext_ids: set[str], certain: bool = True) -> None:
    for c in g.clients:
        if c.uplink_device_external_id in ext_ids:
            via = g.devices[c.uplink_device_external_id].name if c.uplink_device_external_id in g.devices else ""
            imp.client(c, via, certain)


def _network_clients(g: Graph, imp: Impact, n: Network, relation: str) -> None:
    members = [c for c in g.clients if (net := g.network_of(c.ip_address)) is not None and net.id == n.id]
    imp.network(n, relation, len(members) if n.ipv4 else None)
    for c in members:
        imp.client(c, f"in {n.name}")
    if not n.ipv4:
        imp.notes.append(f"{n.name} has no recorded subnet, so its clients cannot be counted.")


def _zone_networks(g: Graph, z: FirewallZone | None) -> list[Network]:
    if z is None:
        return []
    return [n for n in g.networks if n.zone_external_id == z.external_id or n.external_id in (z.network_external_ids or [])]


async def _device_restart(g: Graph, imp: Impact, d: Device) -> None:
    imp.device(d, "target")
    below = g.subtree(d.external_id)
    for child, depth, via in below:
        imp.device(child, "downstream", depth, via)
    _clients_behind(g, imp, {d.external_id, *(c.external_id for c, _, _ in below)})
    if "gateway" in (d.features or []):
        imp.summary.append(f"{d.name} is the gateway: every network on the site loses routing and internet access while it restarts.")
        for n in g.networks:
            _network_clients(g, imp, n, "loses routing")
    else:
        imp.summary.append(
            f"{d.name} and {len(below)} device{'s' if len(below) != 1 else ''} behind it drop off the network while it restarts."
            if below
            else f"Only {d.name} and what attaches to it are affected."
        )
        nets: dict[str, tuple[Network, int]] = {}
        for c in g.clients:
            if c.id in imp.clients and (n := g.network_of(c.ip_address)) is not None:
                nets[n.id] = (n, nets.get(n.id, (n, 0))[1] + 1)
        for n, count in sorted(nets.values(), key=lambda x: -x[1]):
            imp.network(n, "affected", count)


async def _power_cycle(g: Graph, imp: Impact, d: Device, port_idx: int | None) -> None:
    imp.device(d, "target")
    imp.summary.append(f"Whatever port {port_idx} on {d.name} powers loses power for a few seconds and reboots.")
    imp.notes.append(
        "The official API does not report which device is on a switch port, so the devices and clients below are only "
        "possibly affected: they reach the network through this switch."
    )
    near = [child for child in g.children.get(d.external_id, [])]
    for child in near:
        imp.device(child, "possibly", 1, d.name)
    _clients_behind(g, imp, {d.external_id, *(c.external_id for c in near)}, certain=False)


async def _policy(g: Graph, imp: Impact, p: FirewallPolicy, cr: ChangeRequest) -> None:
    imp.policy(p, "target")
    src, dst = g.zones.get(p.source_zone_external_id or ""), g.zones.get(p.destination_zone_external_id or "")
    imp.zone(src, "source")
    imp.zone(dst, "destination")
    pair = f"{src.name if src else 'any zone'} → {dst.name if dst else 'any zone'}"
    if cr.operation == "firewall_policy.set_logging":
        imp.traffic_change = False
        imp.summary.append(
            f"No traffic changes. Matches of “{p.name}” ({pair}) {'start' if cr.params.get('enabled') else 'stop'} being logged."
        )
    else:
        on = bool(cr.params.get("enabled"))
        effect = {"BLOCK": "blocked", "REJECT": "rejected", "ALLOW": "allowed"}.get(p.action.upper(), p.action.lower())
        imp.summary.append(
            f"Traffic {pair} that matches “{p.name}” is {effect} again."
            if on
            else f"Traffic {pair} that matches “{p.name}” is no longer {effect} by it; the next matching policy decides instead."
        )
    others = [
        q
        for q in g.policies
        if q.id != p.id
        and q.source_zone_external_id == p.source_zone_external_id
        and q.destination_zone_external_id == p.destination_zone_external_id
    ]
    for q in sorted(others, key=lambda q: q.index if q.index is not None else 1 << 30):
        imp.policy(q, "same zone pair")
    for z, rel in ((src, "source"), (dst, "destination")):
        for n in _zone_networks(g, z):
            _network_clients(g, imp, n, f"in {rel} zone")


async def _network_edit(g: Graph, imp: Impact, n: Network, cr: ChangeRequest) -> None:
    _network_clients(g, imp, n, "target")
    zone = g.zones.get(n.zone_external_id or "")
    imp.zone(zone, "current zone")
    op = cr.operation
    if op == "network.rename":
        imp.traffic_change = False
        imp.summary.append(f"Only the name changes; {n.name}'s clients keep their addresses and access.")
    elif op == "network.set_internet_access":
        on = bool(cr.params.get("value"))
        imp.summary.append(f"Clients in {n.name} {'regain' if on else 'lose'} internet access.")
    elif op == "network.set_isolation":
        on = bool(cr.params.get("value"))
        imp.summary.append(
            f"Clients in {n.name} {'can no longer reach' if on else 'can reach'} other networks{' (isolation on)' if on else ''}."
        )
    elif op == "network.move_zone":
        new = next((z for z in g.zones.values() if z.external_id == cr.params.get("value")), None)
        imp.zone(new, "new zone")
        imp.summary.append(
            f"{n.name} leaves {zone.name if zone else 'its zone'} for {new.name if new else 'another zone'}: "
            "the policies of the new zone decide its traffic from then on."
        )
        if new is not None:
            for q in g.policies:
                if new.external_id in (q.source_zone_external_id, q.destination_zone_external_id):
                    imp.policy(q, "applies after the move")
    if zone is not None:
        for q in g.policies:
            if zone.external_id in (q.source_zone_external_id, q.destination_zone_external_id):
                imp.policy(q, "applies now" if op == "network.move_zone" else "governs this zone")


async def _zone_rename(g: Graph, imp: Impact, z: FirewallZone) -> None:
    imp.traffic_change = False
    imp.zone(z, "target")
    imp.summary.append("Only the zone's name changes; its networks and policies are untouched.")
    for n in _zone_networks(g, z):
        imp.network(n, "in this zone")
    for q in g.policies:
        if z.external_id in (q.source_zone_external_id, q.destination_zone_external_id):
            imp.policy(q, "refers to this zone")


async def add_change(db: AsyncSession, imp: Impact, cr: ChangeRequest, graphs: dict[str, Graph]) -> None:
    g = graphs.get(cr.site_id) or await load(db, cr.site_id)
    graphs[cr.site_id] = g
    imp.observed_at = max((x for x in (imp.observed_at, g.observed_at) if x is not None), default=None)
    if cr.target_type in ("device", "port"):
        d = await db.get(Device, cr.target_id)
        if d is None:
            imp.notes.append(f"{cr.target_label} is no longer in the inventory.")
            return
        if cr.operation == "port.power_cycle":
            await _power_cycle(g, imp, d, cr.params.get("port_idx"))
        else:
            await _device_restart(g, imp, d)
    elif cr.target_type == "firewall_policy":
        p = await db.get(FirewallPolicy, cr.target_id)
        if p is not None:
            await _policy(g, imp, p, cr)
    elif cr.target_type == "network":
        n = await db.get(Network, cr.target_id)
        if n is not None:
            await _network_edit(g, imp, n, cr)
    elif cr.target_type == "zone":
        z = await db.get(FirewallZone, cr.target_id)
        if z is not None:
            await _zone_rename(g, imp, z)
    else:
        imp.notes.append(f"No impact model for {cr.operation} yet.")


async def change_impact(db: AsyncSession, cr: ChangeRequest) -> dict[str, Any]:
    imp = Impact()
    await add_change(db, imp, cr, {})
    return imp.as_dict()


async def batch_impact(db: AsyncSession, rows: list[ChangeRequest]) -> dict[str, Any]:
    """The union of every item's impact; the per-item sentences are kept, the lists are merged."""
    imp = Impact(traffic_change=False)
    graphs: dict[str, Graph] = {}
    for cr in rows:
        one = Impact()
        await add_change(db, one, cr, graphs)
        imp.traffic_change = imp.traffic_change or one.traffic_change
        imp.summary.extend(s for s in one.summary if s not in imp.summary)
        imp.notes.extend(n for n in one.notes if n not in imp.notes)
        for attr in ("devices", "clients", "networks", "zones", "policies"):
            mine, theirs = getattr(imp, attr), getattr(one, attr)
            for k, v in theirs.items():
                if (
                    k not in mine
                    or (attr == "devices" and mine[k]["relation"] == "possibly")
                    or (attr == "clients" and not mine[k]["certain"])
                ):
                    mine[k] = v
        imp.observed_at = max((x for x in (imp.observed_at, one.observed_at) if x is not None), default=None)
    return imp.as_dict()

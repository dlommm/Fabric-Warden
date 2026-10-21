"""IPAM: documented prefixes, VLANs and reservations versus what the controller reports.

Everything written here is local documentation. Nothing is pushed to a controller;
controller-side changes belong to the change engine (Phase 6).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.api.inventory import _subnets
from warden.models import Client, Device, IpPrefix, IpReservation, Network, Site, VlanRecord
from warden.security.rbac import P

router = APIRouter(prefix="/api/ipam", tags=["ipam"], dependencies=[Depends(require_module("ipam"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.IPAM_EDIT)

IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address
Status = Literal["active", "planned", "reserved", "deprecated"]
MAP_LIMIT = 1024  # largest prefix rendered address-by-address
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


# -- observed snapshot ----------------------------------------------------------


@dataclass
class Holder:
    kind: str  # client | device
    id: str
    name: str
    mac: str | None
    is_demo: bool


@dataclass
class Observed:
    """One read of the observed side, shared by every view in a request."""

    networks: list[tuple[IPNet, Network]]
    holders: dict[IPAddr, list[Holder]] = field(default_factory=dict)
    by_mac: dict[str, IPAddr] = field(default_factory=dict)

    def network_for(self, net: IPNet, site_id: str | None) -> Network | None:
        for n, row in self.networks:
            if n == net and (site_id is None or row.site_id == site_id):
                return row
        return None

    @staticmethod
    def dhcp_range(n: Network | None) -> tuple[IPAddr, IPAddr] | None:
        rng = (((n.ipv4 if n else None) or {}).get("dhcpConfiguration") or {}).get("ipAddressRange") or {}
        if rng.get("start") and rng.get("stop"):
            return ipaddress.ip_address(rng["start"]), ipaddress.ip_address(rng["stop"])
        return None

    @staticmethod
    def gateway(n: Network | None) -> IPAddr | None:
        host = ((n.ipv4 if n else None) or {}).get("hostIpAddress")
        return ipaddress.ip_address(host) if host else None


async def observe(db: AsyncSession) -> Observed:
    obs = Observed(_subnets(list(await db.scalars(select(Network).where(Network.present.is_(True))))))
    for c in await db.scalars(select(Client).where(Client.present.is_(True))):
        _hold(obs, Holder("client", c.id, c.alias or c.name, c.mac_address, c.is_demo), c.ip_address)
    for d in await db.scalars(select(Device).where(Device.present.is_(True))):
        _hold(obs, Holder("device", d.id, d.name, d.mac_address, d.is_demo), d.ip_address)
    return obs


def _hold(obs: Observed, h: Holder, ip: str | None) -> None:
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return
    obs.holders.setdefault(addr, []).append(h)
    if h.mac:
        obs.by_mac[h.mac] = addr


# -- prefixes -------------------------------------------------------------------


def _net(p: IpPrefix) -> IPNet:
    return ipaddress.ip_network(p.cidr)


def _usable(net: IPNet) -> int:
    if net.version == 4 and net.prefixlen <= 30:
        return net.num_addresses - 2
    return net.num_addresses


def _bounds(net: IPNet) -> set[IPAddr]:
    if net.version == 4 and net.prefixlen <= 30:
        return {net.network_address, net.broadcast_address}
    return set()


def _parents(prefixes: list[IpPrefix]) -> dict[str, str | None]:
    """Nearest strictly-containing documented prefix for each prefix."""
    nets = [(p, _net(p)) for p in prefixes]
    out: dict[str, str | None] = {}
    for p, n in nets:
        best: tuple[IpPrefix, IPNet] | None = None
        for q, m in nets:
            if q.id != p.id and m.version == n.version and m != n and n.subnet_of(m):  # type: ignore[arg-type]
                if best is None or m.prefixlen > best[1].prefixlen:
                    best = (q, m)
        out[p.id] = best[0].id if best else None
    return out


def _undocumented(prefixes: list[IpPrefix], obs: Observed) -> list[tuple[IPNet, Network]]:
    """Observed networks with no prefix documenting them (globally or for their site)."""
    return [(n, row) for n, row in obs.networks if not any(_net(p) == n and p.site_id in (None, row.site_id) for p in prefixes)]


def _prefix_view(p: IpPrefix, obs: Observed, reservations: list[IpReservation], sites: dict[str, str]) -> dict[str, Any]:
    net = _net(p)
    observed = obs.network_for(net, p.site_id)
    used: set[IPAddr] = {a for a in obs.holders if a in net}
    used |= {ipaddress.ip_address(r.address) for r in reservations}
    if gw := obs.gateway(observed):
        used.add(gw)
    used -= _bounds(net)
    usable = _usable(net)
    dhcp = obs.dhcp_range(observed)
    return {
        "id": p.id,
        "cidr": p.cidr,
        "family": p.family,
        "vlan_id": p.vlan_id,
        "status": p.status,
        "description": p.description,
        "labels": p.labels,
        "is_demo": p.is_demo,
        "site": {"id": p.site_id, "name": sites.get(p.site_id)} if p.site_id else None,
        "size": net.num_addresses,
        "usable": usable,
        "used": len(used),
        "utilization_pct": round(100 * len(used) / usable, 1) if usable else 0.0,
        "reservations": len(reservations),
        "observed": (
            {
                "network_id": observed.id,
                "name": observed.name,
                "vlan_id": observed.vlan_id,
                "gateway": str(obs.gateway(observed) or "") or None,
                "dhcp_range": {"start": str(dhcp[0]), "stop": str(dhcp[1])} if dhcp else None,
                "dhcp_pool_size": int(dhcp[1]) - int(dhcp[0]) + 1 if dhcp else None,
            }
            if observed
            else None
        ),
        "updated_at": p.updated_at.isoformat(),
    }


async def _load(db: AsyncSession) -> tuple[list[IpPrefix], dict[str, list[IpReservation]], dict[str, str]]:
    prefixes = list(await db.scalars(select(IpPrefix)))
    prefixes.sort(key=lambda p: (p.family, int(_net(p).network_address), _net(p).prefixlen))
    res: dict[str, list[IpReservation]] = {}
    for r in await db.scalars(select(IpReservation)):
        res.setdefault(r.prefix_id, []).append(r)
    sites = {s.id: s.name for s in await db.scalars(select(Site))}
    return prefixes, res, sites


@router.get("/prefixes")
async def list_prefixes(db: DB, principal: Reader) -> dict:
    prefixes, res, sites = await _load(db)
    obs = await observe(db)
    parents = _parents(prefixes)
    depth: dict[str, int] = {}
    for p in prefixes:  # sorted, so a parent is always visited before its children
        depth[p.id] = depth[parents[p.id]] + 1 if parents[p.id] else 0
    items = [_prefix_view(p, obs, res.get(p.id, []), sites) | {"parent_id": parents[p.id], "depth": depth[p.id]} for p in prefixes]
    undocumented = [
        {"network_id": row.id, "name": row.name, "subnet": str(n), "vlan_id": row.vlan_id, "is_demo": row.is_demo}
        for n, row in _undocumented(prefixes, obs)
    ]
    return {"items": items, "total": len(items), "undocumented": undocumented}


class PrefixCreate(BaseModel):
    cidr: str = Field(max_length=49)
    site_id: str | None = None
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    status: Status = "active"
    description: str = Field(default="", max_length=2000)
    labels: list[Annotated[str, Field(min_length=1, max_length=48)]] = Field(default_factory=list, max_length=32)

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: str) -> str:
        try:
            return str(ipaddress.ip_network(v.strip(), strict=False))
        except ValueError as exc:
            raise ValueError("not a valid IPv4 or IPv6 prefix") from exc


class PrefixPatch(BaseModel):
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    status: Status | None = None
    description: str | None = Field(default=None, max_length=2000)
    labels: list[Annotated[str, Field(min_length=1, max_length=48)]] | None = Field(default=None, max_length=32)


async def _site_exists(db: AsyncSession, site_id: str | None) -> None:
    if site_id and await db.get(Site, site_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown site")


async def _duplicate(db: AsyncSession, model: Any, site_id: str | None, **match: Any) -> bool:
    q = select(model).filter_by(**match)
    q = q.where(model.site_id.is_(None)) if site_id is None else q.where(model.site_id == site_id)
    return await db.scalar(q) is not None


@router.post("/prefixes", status_code=status.HTTP_201_CREATED)
async def create_prefix(body: PrefixCreate, db: DB, principal: Editor) -> dict:
    await _site_exists(db, body.site_id)
    if await _duplicate(db, IpPrefix, body.site_id, cidr=body.cidr):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.cidr} is already documented")
    p = IpPrefix(**body.model_dump(), family=ipaddress.ip_network(body.cidr).version)
    p.labels = sorted(set(p.labels))
    db.add(p)
    await db.flush()
    await audit.record(db, principal.actor, "ipam.prefix.create", target_type="ip_prefix", target_id=p.id, detail={"cidr": p.cidr})
    _, _, sites = await _load(db)
    return _prefix_view(p, await observe(db), [], sites)


async def _get_prefix(db: AsyncSession, prefix_id: str) -> IpPrefix:
    p = await db.get(IpPrefix, prefix_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prefix not found")
    return p


@router.patch("/prefixes/{prefix_id}")
async def update_prefix(prefix_id: str, body: PrefixPatch, db: DB, principal: Editor) -> dict:
    p = await _get_prefix(db, prefix_id)
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(p, k, sorted(set(v)) if k == "labels" else v)
    await audit.record(db, principal.actor, "ipam.prefix.update", target_type="ip_prefix", target_id=p.id, detail=fields)
    res = list(await db.scalars(select(IpReservation).where(IpReservation.prefix_id == p.id)))
    _, _, sites = await _load(db)
    return _prefix_view(p, await observe(db), res, sites)


@router.delete("/prefixes/{prefix_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prefix(prefix_id: str, db: DB, principal: Editor) -> None:
    p = await _get_prefix(db, prefix_id)
    n = len(list(await db.scalars(select(IpReservation.id).where(IpReservation.prefix_id == p.id))))
    await audit.record(
        db, principal.actor, "ipam.prefix.delete", target_type="ip_prefix", target_id=p.id, detail={"cidr": p.cidr, "reservations": n}
    )
    await db.delete(p)


class Adopt(BaseModel):
    network_ids: list[str] | None = Field(default=None, max_length=512)


@router.post("/prefixes/adopt")
async def adopt_networks(body: Adopt, db: DB, principal: Editor) -> dict:
    """Document observed networks that have no prefix yet (and their VLANs)."""
    prefixes = list(await db.scalars(select(IpPrefix)))
    created = []
    for n, row in _undocumented(prefixes, await observe(db)):
        if body.network_ids is not None and row.id not in body.network_ids:
            continue
        p = IpPrefix(
            site_id=row.site_id,
            cidr=str(n),
            family=n.version,
            vlan_id=row.vlan_id,
            status="active",
            description=row.name,
            labels=[],
            is_demo=row.is_demo,
        )
        db.add(p)
        created.append(p)
        if row.vlan_id and not await _duplicate(db, VlanRecord, row.site_id, vlan_id=row.vlan_id):
            db.add(VlanRecord(site_id=row.site_id, vlan_id=row.vlan_id, name=row.name, status="active", is_demo=row.is_demo))
    await db.flush()
    await audit.record(db, principal.actor, "ipam.prefix.adopt", target_type="ip_prefix", detail={"cidrs": [p.cidr for p in created]})
    return {"created": [{"id": p.id, "cidr": p.cidr} for p in created]}


# -- prefix detail and address map --------------------------------------------------


def _address_map(net: IPNet, obs: Observed, observed: Network | None, reservations: list[IpReservation]) -> list[dict[str, Any]]:
    by_addr = {ipaddress.ip_address(r.address): r for r in reservations}
    gw = obs.gateway(observed)
    dhcp = obs.dhcp_range(observed)
    bounds = _bounds(net)
    out = []
    for a in net:
        holders = obs.holders.get(a, [])
        r = by_addr.get(a)
        if a in bounds:
            state = "network" if a == net.network_address else "broadcast"
        elif a == gw:
            state = "gateway"
        elif holders:
            state = "observed"
        elif r:
            state = "reserved"
        elif dhcp and dhcp[0] <= a <= dhcp[1]:
            state = "dhcp"
        else:
            state = "free"
        conflict = bool(r and r.mac_address and any(h.mac and h.mac != r.mac_address for h in holders)) or len(holders) > 1
        entry: dict[str, Any] = {"address": str(a), "state": state, "in_dhcp_pool": bool(dhcp and dhcp[0] <= a <= dhcp[1])}
        if holders:
            entry["holders"] = [{"kind": h.kind, "id": h.id, "name": h.name, "mac": h.mac} for h in holders]
        if r:
            entry["reservation"] = {"id": r.id, "hostname": r.hostname, "kind": r.kind, "mac": r.mac_address}
        if conflict:
            entry["conflict"] = True
        out.append(entry)
    return out


def _reservation(r: IpReservation, obs: Observed) -> dict[str, Any]:
    addr = ipaddress.ip_address(r.address)
    holders = obs.holders.get(addr, [])
    seen_at = obs.by_mac.get(r.mac_address) if r.mac_address else None
    if not holders:
        state = "idle" if seen_at is None else "elsewhere"
    elif r.mac_address and all(h.mac != r.mac_address for h in holders):
        state = "conflict"
    else:
        state = "in_use"
    return {
        "id": r.id,
        "prefix_id": r.prefix_id,
        "address": r.address,
        "kind": r.kind,
        "mac_address": r.mac_address,
        "hostname": r.hostname,
        "description": r.description,
        "is_demo": r.is_demo,
        "state": state,
        "observed_holders": [{"kind": h.kind, "id": h.id, "name": h.name, "mac": h.mac} for h in holders],
        "mac_seen_at": str(seen_at) if seen_at and seen_at != addr else None,
    }


@router.get("/prefixes/{prefix_id}")
async def get_prefix(prefix_id: str, db: DB, principal: Reader) -> dict:
    p = await _get_prefix(db, prefix_id)
    prefixes, res, sites = await _load(db)
    obs = await observe(db)
    net = _net(p)
    observed = obs.network_for(net, p.site_id)
    parents = _parents(prefixes)
    mine = sorted(res.get(p.id, []), key=lambda r: int(ipaddress.ip_address(r.address)))
    amap = _address_map(net, obs, observed, mine) if net.num_addresses <= MAP_LIMIT else None
    free = [e["address"] for e in amap or [] if e["state"] == "free"]
    by_id = {x.id: x for x in prefixes}
    chain = []
    cur = parents.get(p.id)
    while cur:
        chain.insert(0, {"id": cur, "cidr": by_id[cur].cidr})
        cur = parents.get(cur)
    return _prefix_view(p, obs, mine, sites) | {
        "parents": chain,
        "children": [{"id": x.id, "cidr": x.cidr, "status": x.status} for x in prefixes if parents.get(x.id) == p.id],
        "reservation_items": [_reservation(r, obs) for r in mine],
        "address_map": amap,
        "address_map_omitted": None if amap is not None else f"prefix larger than {MAP_LIMIT} addresses",
        "next_free": free[0] if free else None,
        "free_count": len(free) if amap is not None else None,
    }


# -- reservations ---------------------------------------------------------------------


class ReservationBody(BaseModel):
    address: str | None = Field(default=None, max_length=45)
    kind: Literal["static", "dhcp", "infrastructure"] | None = None
    mac_address: str | None = Field(default=None, max_length=17)
    hostname: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("mac_address")
    @classmethod
    def _mac(cls, v: str | None) -> str | None:
        if not v or not v.strip():
            return None
        mac = v.strip().lower().replace("-", ":")
        if not MAC_RE.match(mac):
            raise ValueError("MAC address must look like aa:bb:cc:dd:ee:ff")
        return mac


def _check_address(net: IPNet, raw: str) -> str:
    try:
        addr = ipaddress.ip_address(raw.strip())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a valid IP address") from exc
    if addr not in net:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{addr} is outside {net}")
    if addr in _bounds(net):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{addr} is the network or broadcast address")
    return str(addr)


async def _taken(db: AsyncSession, prefix_id: str, address: str, exclude: str | None = None) -> None:
    q = select(IpReservation).where(IpReservation.prefix_id == prefix_id, IpReservation.address == address)
    other = await db.scalar(q)
    if other is not None and other.id != exclude:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{address} is already reserved")


@router.post("/prefixes/{prefix_id}/reservations", status_code=status.HTTP_201_CREATED)
async def create_reservation(prefix_id: str, body: ReservationBody, db: DB, principal: Editor) -> dict:
    p = await _get_prefix(db, prefix_id)
    if not body.address:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "address is required")
    address = _check_address(_net(p), body.address)
    await _taken(db, p.id, address)
    r = IpReservation(
        prefix_id=p.id,
        address=address,
        kind=body.kind or "static",
        mac_address=body.mac_address,
        hostname=(body.hostname or "").strip(),
        description=body.description or "",
    )
    db.add(r)
    await db.flush()
    await audit.record(
        db, principal.actor, "ipam.reservation.create", target_type="ip_reservation", target_id=r.id, detail={"address": address}
    )
    return _reservation(r, await observe(db))


async def _get_reservation(db: AsyncSession, reservation_id: str) -> IpReservation:
    r = await db.get(IpReservation, reservation_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reservation not found")
    return r


@router.patch("/reservations/{reservation_id}")
async def update_reservation(reservation_id: str, body: ReservationBody, db: DB, principal: Editor) -> dict:
    r = await _get_reservation(db, reservation_id)
    fields = body.model_dump(exclude_unset=True)
    if "address" in fields and fields["address"]:
        p = await _get_prefix(db, r.prefix_id)
        fields["address"] = _check_address(_net(p), fields["address"])
        await _taken(db, p.id, fields["address"], exclude=r.id)
    for k, v in fields.items():
        if k == "address" and not v:
            continue
        setattr(r, k, (v or "") if k in ("hostname", "description") else v)
    await audit.record(db, principal.actor, "ipam.reservation.update", target_type="ip_reservation", target_id=r.id, detail=fields)
    return _reservation(r, await observe(db))


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reservation(reservation_id: str, db: DB, principal: Editor) -> None:
    r = await _get_reservation(db, reservation_id)
    await audit.record(
        db, principal.actor, "ipam.reservation.delete", target_type="ip_reservation", target_id=r.id, detail={"address": r.address}
    )
    await db.delete(r)


# -- VLANs ------------------------------------------------------------------------------


@router.get("/vlans")
async def list_vlans(db: DB, principal: Reader) -> dict:
    records = list(await db.scalars(select(VlanRecord)))
    networks = list(await db.scalars(select(Network).where(Network.present.is_(True), Network.vlan_id.is_not(None))))
    prefixes = list(await db.scalars(select(IpPrefix).where(IpPrefix.vlan_id.is_not(None))))
    ids = sorted({r.vlan_id for r in records} | {n.vlan_id for n in networks if n.vlan_id} | {p.vlan_id for p in prefixes if p.vlan_id})
    items = []
    for vid in ids:
        recs = [r for r in records if r.vlan_id == vid]
        nets = [n for n in networks if n.vlan_id == vid]
        rec = recs[0] if recs else None
        if rec and nets:
            state = "deprecated_in_use" if rec.status == "deprecated" else "documented"
        elif rec:
            state = "unobserved" if rec.status == "active" else rec.status
        else:
            state = "undocumented"
        items.append(
            {
                "vlan_id": vid,
                "record": (
                    {"id": rec.id, "name": rec.name, "status": rec.status, "description": rec.description, "is_demo": rec.is_demo}
                    if rec
                    else None
                ),
                "state": state,
                "observed_networks": [
                    {"id": n.id, "name": n.name, "subnet": _cidr_of(n), "enabled": n.enabled, "is_demo": n.is_demo} for n in nets
                ],
                "prefixes": [{"id": p.id, "cidr": p.cidr, "status": p.status} for p in prefixes if p.vlan_id == vid],
                "name_mismatch": bool(rec and nets and all(n.name.strip().lower() != rec.name.strip().lower() for n in nets)),
            }
        )
    return {"items": items, "total": len(items)}


def _cidr_of(n: Network) -> str | None:
    cfg = n.ipv4 or {}
    if cfg.get("hostIpAddress") and cfg.get("prefixLength"):
        return str(ipaddress.ip_network(f"{cfg['hostIpAddress']}/{cfg['prefixLength']}", strict=False))
    return None


class VlanCreate(BaseModel):
    vlan_id: int = Field(ge=1, le=4094)
    name: str = Field(min_length=1, max_length=128)
    site_id: str | None = None
    status: Status = "active"
    description: str = Field(default="", max_length=2000)


class VlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Status | None = None
    description: str | None = Field(default=None, max_length=2000)


@router.post("/vlans", status_code=status.HTTP_201_CREATED)
async def create_vlan(body: VlanCreate, db: DB, principal: Editor) -> dict:
    await _site_exists(db, body.site_id)
    if await _duplicate(db, VlanRecord, body.site_id, vlan_id=body.vlan_id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"VLAN {body.vlan_id} is already documented")
    v = VlanRecord(**body.model_dump())
    db.add(v)
    await db.flush()
    await audit.record(db, principal.actor, "ipam.vlan.create", target_type="vlan_record", target_id=v.id, detail={"vlan_id": v.vlan_id})
    return {"id": v.id, "vlan_id": v.vlan_id, "name": v.name, "status": v.status, "description": v.description}


@router.patch("/vlans/{record_id}")
async def update_vlan(record_id: str, body: VlanPatch, db: DB, principal: Editor) -> dict:
    v = await db.get(VlanRecord, record_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VLAN record not found")
    fields = body.model_dump(exclude_unset=True)
    for k, val in fields.items():
        setattr(v, k, val)
    await audit.record(db, principal.actor, "ipam.vlan.update", target_type="vlan_record", target_id=v.id, detail=fields)
    return {"id": v.id, "vlan_id": v.vlan_id, "name": v.name, "status": v.status, "description": v.description}


@router.delete("/vlans/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vlan(record_id: str, db: DB, principal: Editor) -> None:
    v = await db.get(VlanRecord, record_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VLAN record not found")
    await audit.record(db, principal.actor, "ipam.vlan.delete", target_type="vlan_record", target_id=v.id, detail={"vlan_id": v.vlan_id})
    await db.delete(v)


# -- planned vs observed ---------------------------------------------------------------


def _finding(kind: str, severity: str, title: str, detail: str, **refs: Any) -> dict[str, Any]:
    key = ":".join([kind, *(str(v) for v in refs.values())])
    return {"key": key, "kind": kind, "severity": severity, "title": title, "detail": detail, "refs": refs}


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    """Planned-vs-observed reconciliation. Severity: critical > warning > info."""
    prefixes, res, _ = await _load(db)
    obs = await observe(db)
    out: list[dict[str, Any]] = []
    nets = [(p, _net(p)) for p in prefixes]

    for p, net in nets:
        observed = obs.network_for(net, p.site_id)
        contains_observed = any(n != net and n.version == net.version and n.subnet_of(net) for n, _ in obs.networks)  # type: ignore[arg-type]
        if observed is None and p.status == "active" and not contains_observed:
            out.append(
                _finding(
                    "prefix_unobserved",
                    "warning",
                    f"{p.cidr} is documented as active but no controller reports it",
                    "Mark it planned or deprecated, or check the network on the controller.",
                    prefix_id=p.id,
                )
            )
        if observed is not None and p.status == "deprecated":
            out.append(
                _finding(
                    "deprecated_in_use",
                    "warning",
                    f"{p.cidr} is deprecated but still configured as {observed.name}",
                    "Retire the network or update the documented status.",
                    prefix_id=p.id,
                    network_id=observed.id,
                )
            )
        if observed is not None and p.vlan_id and observed.vlan_id and p.vlan_id != observed.vlan_id:
            out.append(
                _finding(
                    "vlan_mismatch",
                    "warning",
                    f"{p.cidr}: documented VLAN {p.vlan_id}, controller uses VLAN {observed.vlan_id}",
                    f"Observed network {observed.name}.",
                    prefix_id=p.id,
                    network_id=observed.id,
                )
            )
        dhcp = obs.dhcp_range(observed)
        for r in res.get(p.id, []):
            addr = ipaddress.ip_address(r.address)
            holders = obs.holders.get(addr, [])
            if r.mac_address and holders and all(h.mac != r.mac_address for h in holders):
                who = ", ".join(h.name for h in holders)
                out.append(
                    _finding(
                        "reservation_conflict",
                        "critical",
                        f"{r.address} is reserved for {r.hostname or r.mac_address} but held by {who}",
                        f"Reserved MAC {r.mac_address}; observed {', '.join(h.mac or '?' for h in holders)}.",
                        prefix_id=p.id,
                        reservation_id=r.id,
                    )
                )
            elif r.mac_address and not holders and (seen := obs.by_mac.get(r.mac_address)) is not None:
                out.append(
                    _finding(
                        "reservation_elsewhere",
                        "info",
                        f"{r.hostname or r.mac_address} is reserved at {r.address} but observed at {seen}",
                        "The device is not using its documented address.",
                        prefix_id=p.id,
                        reservation_id=r.id,
                    )
                )
            if r.kind != "dhcp" and dhcp and dhcp[0] <= addr <= dhcp[1]:
                out.append(
                    _finding(
                        "reservation_in_pool",
                        "warning",
                        f"{r.address} is a {r.kind} reservation inside the DHCP pool",
                        f"Pool {dhcp[0]}–{dhcp[1]}; the controller can lease this address to another client.",
                        prefix_id=p.id,
                        reservation_id=r.id,
                    )
                )

    for n, row in _undocumented(prefixes, obs):
        out.append(
            _finding(
                "network_undocumented",
                "info",
                f"{row.name} ({n}) has no documented prefix",
                "Adopt it into the IP plan to track utilisation and reservations.",
                network_id=row.id,
            )
        )

    known = [n for _, n in nets] + [n for n, _ in obs.networks]
    for addr, holders in sorted(obs.holders.items(), key=lambda kv: (kv[0].version, int(kv[0]))):
        if len(holders) > 1:
            out.append(
                _finding(
                    "duplicate_address",
                    "critical",
                    f"{addr} is reported for {len(holders)} endpoints",
                    ", ".join(f"{h.name} ({h.kind})" for h in holders),
                    address=str(addr),
                )
            )
        if not any(addr in n for n in known if n.version == addr.version):
            out.append(
                _finding(
                    "address_outside_plan",
                    "warning",
                    f"{holders[0].name} uses {addr}, outside every known prefix",
                    "Static misconfiguration or an undocumented network.",
                    address=str(addr),
                )
            )

    rank = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (rank[f["severity"]], f["kind"], f["title"]))
    counts = {s: sum(1 for f in out if f["severity"] == s) for s in rank}
    return {"items": out, "counts": counts}

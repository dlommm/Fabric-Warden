"""Rack & DCIM: locations, racks, rack items, elevations and occupancy validation.

Everything here is local documentation. Rack items may link to an observed
device so the elevation shows live state, but nothing is pushed anywhere.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.models import Device, Location, Rack, RackItem, Site
from warden.security.rbac import P

router = APIRouter(prefix="/api/dcim", tags=["dcim"], dependencies=[Depends(require_module("dcim"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.DCIM_EDIT)

LocationKind = Literal["site", "building", "floor", "room", "row", "cage"]
RackStatus = Literal["active", "planned", "retired"]
ItemKind = Literal["device", "server", "patch_panel", "pdu", "ups", "shelf", "cable_manager", "blank", "other"]
ItemStatus = Literal["installed", "planned", "removed"]
Face = Literal["front", "rear"]
INFRA_FEATURES = {"gateway", "switching"}  # devices expected to live in a rack


# -- locations ------------------------------------------------------------------------


def _location(loc: Location, racks: int = 0) -> dict[str, Any]:
    return {
        "id": loc.id,
        "parent_id": loc.parent_id,
        "site_id": loc.site_id,
        "kind": loc.kind,
        "name": loc.name,
        "description": loc.description,
        "rack_count": racks,
        "is_demo": loc.is_demo,
    }


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: LocationKind = "room"
    parent_id: str | None = None
    site_id: str | None = None
    description: str = Field(default="", max_length=2000)


class LocationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: LocationKind | None = None
    parent_id: str | None = None
    site_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)


async def _get(db: AsyncSession, model: Any, id: str | None, what: str) -> Any:
    row = await db.get(model, id) if id else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return row


async def _check_parent(db: AsyncSession, loc_id: str | None, parent_id: str | None) -> None:
    """Parent must exist and must not be the location itself or one of its descendants."""
    seen = set()
    cur = parent_id
    while cur:
        if cur == loc_id or cur in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A location cannot be nested inside itself")
        seen.add(cur)
        parent = await _get(db, Location, cur, "Parent location")
        cur = parent.parent_id


@router.get("/locations")
async def list_locations(db: DB, principal: Reader) -> dict:
    counts = dict((await db.execute(select(Rack.location_id, func.count()).group_by(Rack.location_id))).all())
    locs = await db.scalars(select(Location).order_by(Location.name))
    return {"items": [_location(loc, counts.get(loc.id, 0)) for loc in locs]}


@router.post("/locations", status_code=status.HTTP_201_CREATED)
async def create_location(body: LocationCreate, db: DB, principal: Editor) -> dict:
    await _check_parent(db, None, body.parent_id)
    if body.site_id:
        await _get(db, Site, body.site_id, "Site")
    loc = Location(**body.model_dump())
    db.add(loc)
    await db.flush()
    await audit.record(db, principal.actor, "dcim.location.create", target_type="location", target_id=loc.id, detail={"name": loc.name})
    return _location(loc)


@router.patch("/locations/{location_id}")
async def update_location(location_id: str, body: LocationPatch, db: DB, principal: Editor) -> dict:
    loc = await _get(db, Location, location_id, "Location")
    fields = body.model_dump(exclude_unset=True)
    if "parent_id" in fields:
        await _check_parent(db, loc.id, fields["parent_id"])
    if fields.get("site_id"):
        await _get(db, Site, fields["site_id"], "Site")
    for k, v in fields.items():
        setattr(loc, k, v)
    await audit.record(db, principal.actor, "dcim.location.update", target_type="location", target_id=loc.id, detail=fields)
    return _location(loc)


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(location_id: str, db: DB, principal: Editor) -> None:
    loc = await _get(db, Location, location_id, "Location")
    if await db.scalar(select(func.count()).select_from(Location).where(Location.parent_id == loc.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Move or delete the locations inside it first")
    await audit.record(db, principal.actor, "dcim.location.delete", target_type="location", target_id=loc.id, detail={"name": loc.name})
    await db.delete(loc)  # racks keep existing with no location


# -- racks ----------------------------------------------------------------------------


def _faces(item: RackItem) -> set[str]:
    return {"front", "rear"} if item.full_depth else {item.face}


def _units(item: RackItem) -> range:
    assert item.position is not None
    return range(item.position, item.position + item.height_u)


def _placed(items: list[RackItem]) -> list[RackItem]:
    return [i for i in items if i.position is not None and i.status != "removed"]


def _occupancy(rack: Rack, items: list[RackItem]) -> dict[str, Any]:
    placed = _placed(items)
    used = {u for i in placed for u in _units(i) if 1 <= u <= rack.u_height}
    planned = {u for i in placed if i.status == "planned" for u in _units(i)}
    draw = sum(i.power_draw_w or 0 for i in items if i.status == "installed" and i.kind not in ("ups", "pdu"))
    return {
        "used_u": len(used),
        "planned_u": len(planned & used),
        "free_u": rack.u_height - len(used),
        "utilisation_pct": round(100 * len(used) / rack.u_height, 1) if rack.u_height else 0.0,
        "documented_draw_w": draw,
    }


def _rack(rack: Rack, items: list[RackItem], locations: dict[str, Location]) -> dict[str, Any]:
    loc = locations.get(rack.location_id or "")
    return {
        "id": rack.id,
        "name": rack.name,
        "location_id": rack.location_id,
        "location": loc.name if loc else None,
        "u_height": rack.u_height,
        "descending_units": rack.descending_units,
        "width_in": rack.width_in,
        "depth_mm": rack.depth_mm,
        "status": rack.status,
        "description": rack.description,
        "item_count": len([i for i in items if i.status != "removed"]),
        "occupancy": _occupancy(rack, items),
        "is_demo": rack.is_demo,
    }


async def _items_by_rack(db: AsyncSession) -> dict[str, list[RackItem]]:
    out: dict[str, list[RackItem]] = {}
    for i in await db.scalars(select(RackItem)):
        out.setdefault(i.rack_id, []).append(i)
    return out


class RackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location_id: str | None = None
    u_height: int = Field(default=42, ge=1, le=60)
    descending_units: bool = False
    width_in: Literal[10, 19, 23] = 19
    depth_mm: int | None = Field(default=None, ge=100, le=2000)
    status: RackStatus = "active"
    description: str = Field(default="", max_length=2000)


class RackPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location_id: str | None = None
    u_height: int | None = Field(default=None, ge=1, le=60)
    descending_units: bool | None = None
    width_in: Literal[10, 19, 23] | None = None
    depth_mm: int | None = Field(default=None, ge=100, le=2000)
    status: RackStatus | None = None
    description: str | None = Field(default=None, max_length=2000)


@router.get("/racks")
async def list_racks(db: DB, principal: Reader) -> dict:
    by_rack = await _items_by_rack(db)
    locations = {loc.id: loc for loc in await db.scalars(select(Location))}
    racks = await db.scalars(select(Rack).order_by(Rack.name))
    return {"items": [_rack(r, by_rack.get(r.id, []), locations) for r in racks]}


@router.post("/racks", status_code=status.HTTP_201_CREATED)
async def create_rack(body: RackCreate, db: DB, principal: Editor) -> dict:
    if body.location_id:
        await _get(db, Location, body.location_id, "Location")
    rack = Rack(**body.model_dump())
    db.add(rack)
    await db.flush()
    await audit.record(db, principal.actor, "dcim.rack.create", target_type="rack", target_id=rack.id, detail={"name": rack.name})
    return _rack(rack, [], {})


@router.patch("/racks/{rack_id}")
async def update_rack(rack_id: str, body: RackPatch, db: DB, principal: Editor) -> dict:
    rack = await _get(db, Rack, rack_id, "Rack")
    fields = body.model_dump(exclude_unset=True)
    if fields.get("location_id"):
        await _get(db, Location, fields["location_id"], "Location")
    items = list(await db.scalars(select(RackItem).where(RackItem.rack_id == rack.id)))
    if "u_height" in fields:
        top = max((max(_units(i)) for i in _placed(items)), default=0)
        if fields["u_height"] < top:
            raise HTTPException(status.HTTP_409_CONFLICT, f"An item occupies U{top}; the rack cannot be shorter than that")
    for k, v in fields.items():
        setattr(rack, k, v)
    await audit.record(db, principal.actor, "dcim.rack.update", target_type="rack", target_id=rack.id, detail=fields)
    locations = {loc.id: loc for loc in await db.scalars(select(Location))}
    return _rack(rack, items, locations)


@router.delete("/racks/{rack_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rack(rack_id: str, db: DB, principal: Editor) -> None:
    rack = await _get(db, Rack, rack_id, "Rack")
    await audit.record(db, principal.actor, "dcim.rack.delete", target_type="rack", target_id=rack.id, detail={"name": rack.name})
    await db.delete(rack)


def _item(i: RackItem, devices: dict[str, Device]) -> dict[str, Any]:
    d = devices.get(i.device_id or "")
    return {
        "id": i.id,
        "rack_id": i.rack_id,
        "name": i.name,
        "kind": i.kind,
        "position": i.position,
        "height_u": i.height_u,
        "face": i.face,
        "full_depth": i.full_depth,
        "status": i.status,
        "device_id": i.device_id,
        "device": (
            {"id": d.id, "name": d.name, "model": d.model, "state": d.state, "ip_address": d.ip_address, "present": d.present}
            if d
            else None
        ),
        "manufacturer": i.manufacturer,
        "model": i.model,
        "serial": i.serial,
        "port_count": i.port_count,
        "connector": i.connector,
        "power_draw_w": i.power_draw_w,
        "power_capacity_w": i.power_capacity_w,
        "outlet_count": i.outlet_count,
        "runtime_full_load_min": i.runtime_full_load_min,
        "notes": i.notes,
        "is_demo": i.is_demo,
    }


@router.get("/racks/{rack_id}")
async def get_rack(rack_id: str, db: DB, principal: Reader) -> dict:
    """Rack with its elevation: one row per unit, top first, with what occupies each face."""
    rack = await _get(db, Rack, rack_id, "Rack")
    items = list(await db.scalars(select(RackItem).where(RackItem.rack_id == rack.id).order_by(RackItem.position.desc())))
    ids = [i.device_id for i in items if i.device_id]
    devices = {d.id: d for d in await db.scalars(select(Device).where(Device.id.in_(ids)))} if ids else {}
    locations = {loc.id: loc for loc in await db.scalars(select(Location))}
    placed = _placed(items)
    units = []
    for u in range(rack.u_height, 0, -1):
        row: dict[str, Any] = {"unit": u, "label": rack.u_height - u + 1 if rack.descending_units else u, "front": None, "rear": None}
        for i in placed:
            if u in _units(i):
                for f in _faces(i):
                    row[f] = i.id
        units.append(row)
    return {
        **_rack(rack, items, locations),
        "units": units,
        "items": [_item(i, devices) for i in items],
        "unplaced": [i.id for i in items if i.position is None and i.status != "removed"],
        "conflicts": _conflicts(rack, items),
    }


def _conflicts(rack: Rack, items: list[RackItem]) -> list[dict[str, Any]]:
    out = []
    placed = _placed(items)
    for n, a in enumerate(placed):
        if max(_units(a)) > rack.u_height or a.position < 1:  # type: ignore[operator]
            out.append({"kind": "out_of_bounds", "items": [a.id], "detail": f"{a.name} extends outside U1–U{rack.u_height}"})
        for b in placed[n + 1 :]:
            shared = set(_units(a)) & set(_units(b))
            if shared and _faces(a) & _faces(b):
                out.append({"kind": "overlap", "items": [a.id, b.id], "detail": f"{a.name} and {b.name} both occupy U{min(shared)}"})
    return out


# -- rack items -----------------------------------------------------------------------


class ItemBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: ItemKind = "device"
    position: int | None = Field(default=None, ge=1, le=60)
    height_u: int = Field(default=1, ge=1, le=20)
    face: Face = "front"
    full_depth: bool = True
    status: ItemStatus = "installed"
    device_id: str | None = None
    manufacturer: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=64)
    serial: str = Field(default="", max_length=64)
    port_count: int = Field(default=0, ge=0, le=288)
    connector: str = Field(default="", max_length=16)
    power_draw_w: int | None = Field(default=None, ge=0, le=100_000)
    power_capacity_w: int | None = Field(default=None, ge=0, le=1_000_000)
    outlet_count: int = Field(default=0, ge=0, le=96)
    runtime_full_load_min: float | None = Field(default=None, ge=0, le=10_000)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def _panel_ports(self) -> ItemBody:
        if self.kind == "patch_panel" and self.port_count < 1:
            raise ValueError("A patch panel needs at least one port")
        return self


class ItemCreate(ItemBody):
    rack_id: str


class ItemPatch(BaseModel):
    rack_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: ItemKind | None = None
    position: int | None = Field(default=None, ge=1, le=60)
    height_u: int | None = Field(default=None, ge=1, le=20)
    face: Face | None = None
    full_depth: bool | None = None
    status: ItemStatus | None = None
    device_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    serial: str | None = Field(default=None, max_length=64)
    port_count: int | None = Field(default=None, ge=0, le=288)
    connector: str | None = Field(default=None, max_length=16)
    power_draw_w: int | None = Field(default=None, ge=0, le=100_000)
    power_capacity_w: int | None = Field(default=None, ge=0, le=1_000_000)
    outlet_count: int | None = Field(default=None, ge=0, le=96)
    runtime_full_load_min: float | None = Field(default=None, ge=0, le=10_000)
    notes: str | None = Field(default=None, max_length=4000)


async def _validate_item(db: AsyncSession, item: RackItem) -> None:
    """Reject placements that overlap another item or fall outside the rack."""
    rack = await _get(db, Rack, item.rack_id, "Rack")
    if item.device_id:
        await _get(db, Device, item.device_id, "Device")
        other = await db.scalar(select(RackItem).where(RackItem.device_id == item.device_id, RackItem.id != item.id))
        if other is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"That device is already placed as {other.name}")
    if item.position is None or item.status == "removed":
        return
    top = item.position + item.height_u - 1
    if top > rack.u_height:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{item.name} would extend to U{top}; {rack.name} has {rack.u_height} U")
    others = await db.scalars(select(RackItem).where(RackItem.rack_id == rack.id, RackItem.id != item.id))
    for o in _placed(list(others)):
        shared = set(_units(item)) & set(_units(o))
        if shared and _faces(item) & _faces(o):
            raise HTTPException(status.HTTP_409_CONFLICT, f"U{min(shared)} is occupied by {o.name}")


async def _item_out(db: AsyncSession, item: RackItem) -> dict[str, Any]:
    devices = {}
    if item.device_id:
        d = await db.get(Device, item.device_id)
        devices = {d.id: d} if d else {}
    return _item(item, devices)


@router.get("/items")
async def list_items(db: DB, principal: Reader, kind: ItemKind | None = None) -> dict:
    """Every rack item with its rack name, for pickers (cable ends, power links, assets)."""
    stmt = select(RackItem).order_by(RackItem.name)
    if kind:
        stmt = stmt.where(RackItem.kind == kind)
    racks = {r.id: r.name for r in await db.scalars(select(Rack))}
    items = list(await db.scalars(stmt))
    ids = [i.device_id for i in items if i.device_id]
    devices = {d.id: d for d in await db.scalars(select(Device).where(Device.id.in_(ids)))} if ids else {}
    return {"items": [{**_item(i, devices), "rack": racks.get(i.rack_id)} for i in items]}


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemCreate, db: DB, principal: Editor) -> dict:
    item = RackItem(**body.model_dump())
    await _validate_item(db, item)
    db.add(item)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "dcim.item.create",
        target_type="rack_item",
        target_id=item.id,
        detail={"name": item.name, "rack_id": item.rack_id},
    )
    return await _item_out(db, item)


@router.patch("/items/{item_id}")
async def update_item(item_id: str, body: ItemPatch, db: DB, principal: Editor) -> dict:
    """Also the move operation: send rack_id/position/face to drag an item elsewhere."""
    item = await _get(db, RackItem, item_id, "Rack item")
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(item, k, v)
    if item.kind == "patch_panel" and item.port_count < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A patch panel needs at least one port")
    await _validate_item(db, item)
    await audit.record(db, principal.actor, "dcim.item.update", target_type="rack_item", target_id=item.id, detail=fields)
    return await _item_out(db, item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, db: DB, principal: Editor) -> None:
    item = await _get(db, RackItem, item_id, "Rack item")
    await audit.record(db, principal.actor, "dcim.item.delete", target_type="rack_item", target_id=item.id, detail={"name": item.name})
    await db.delete(item)


@router.get("/devices/{device_id}/placement")
async def device_placement(device_id: str, db: DB, principal: Reader) -> dict:
    """Where an observed device is racked, for the device detail page."""
    item = await db.scalar(select(RackItem).where(RackItem.device_id == device_id))
    if item is None:
        return {"placement": None}
    rack = await db.get(Rack, item.rack_id)
    loc = await db.get(Location, rack.location_id) if rack and rack.location_id else None
    return {
        "placement": {
            "item_id": item.id,
            "rack_id": item.rack_id,
            "rack": rack.name if rack else None,
            "location": loc.name if loc else None,
            "position": item.position,
            "height_u": item.height_u,
            "face": item.face,
        }
    }


# -- findings -------------------------------------------------------------------------


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    out: list[dict[str, Any]] = []
    by_rack = await _items_by_rack(db)
    racks = {r.id: r for r in await db.scalars(select(Rack))}
    for rack_id, items in by_rack.items():
        rack = racks[rack_id]
        for c in _conflicts(rack, items):
            out.append(
                {
                    "key": f"{c['kind']}:{':'.join(c['items'])}",
                    "kind": c["kind"],
                    "severity": "warning",
                    "title": c["detail"],
                    "rack_id": rack.id,
                }
            )
    linked = {i.device_id: i for items in by_rack.values() for i in items if i.device_id}
    devices = list(await db.scalars(select(Device)))
    for d in devices:
        item = linked.get(d.id)
        if item and not d.present:
            out.append(
                {
                    "key": f"racked_missing:{item.id}",
                    "kind": "racked_missing",
                    "severity": "warning",
                    "title": f"{item.name} is racked but {d.name} is no longer reported by the controller",
                    "rack_id": item.rack_id,
                }
            )
        elif not item and d.present and INFRA_FEATURES & set(d.features or []):
            out.append(
                {
                    "key": f"unracked:{d.id}",
                    "kind": "unracked",
                    "severity": "info",
                    "title": f"{d.name} ({d.model}) is not placed in any rack",
                    "device_id": d.id,
                }
            )
    return {"items": out}

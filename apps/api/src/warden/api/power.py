"""Power & environment: feeds, UPS/PDU chains, load roll-up and headroom.

Loads come from documented typical draw on rack items. Two figures are
computed for every source: *normal* (a load with N inputs splits its draw
evenly) and *failover* (every input must carry the full draw, which is what
happens when the other feed dies). Projected figures add planned items.

The official UniFi API reports PoE state and standard per port but no
wattage, so PoE load is an upper bound from the negotiated standard.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.models import Device, Location, Port, PowerFeed, PowerLink, Rack, RackItem
from warden.security.rbac import P

router = APIRouter(prefix="/api/power", tags=["power"], dependencies=[Depends(require_module("power"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.DCIM_EDIT)

SOURCES = {"ups", "pdu"}
WARN_PCT = 80.0
LOW_RUNTIME_MIN = 10.0
# Maximum power a PSE port may source per standard/type (IEEE 802.3af/at/bt).
POE_MAX_W = {("802.3af", 1): 15.4, ("802.3at", 2): 30.0, ("802.3bt", 3): 60.0, ("802.3bt", 4): 90.0}
POE_BY_STANDARD = {"802.3af": 15.4, "802.3at": 30.0, "802.3bt": 60.0}


# -- feeds ----------------------------------------------------------------------------


class FeedBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location_id: str | None = None
    voltage_v: int = Field(default=230, ge=12, le=480)
    amperage_a: int = Field(default=16, ge=1, le=400)
    max_utilisation_pct: int = Field(default=80, ge=10, le=100)
    description: str = Field(default="", max_length=2000)


class FeedPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location_id: str | None = None
    voltage_v: int | None = Field(default=None, ge=12, le=480)
    amperage_a: int | None = Field(default=None, ge=1, le=400)
    max_utilisation_pct: int | None = Field(default=None, ge=10, le=100)
    description: str | None = Field(default=None, max_length=2000)


def feed_capacity_w(f: PowerFeed) -> float:
    """Usable continuous capacity: V × A × derating."""
    return f.voltage_v * f.amperage_a * f.max_utilisation_pct / 100


async def _get(db: AsyncSession, model: Any, id: str | None, what: str) -> Any:
    row = await db.get(model, id) if id else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return row


@router.post("/feeds", status_code=status.HTTP_201_CREATED)
async def create_feed(body: FeedBody, db: DB, principal: Editor) -> dict:
    if body.location_id:
        await _get(db, Location, body.location_id, "Location")
    f = PowerFeed(**body.model_dump())
    db.add(f)
    await db.flush()
    await audit.record(db, principal.actor, "power.feed.create", target_type="power_feed", target_id=f.id, detail={"name": f.name})
    return {"id": f.id, "name": f.name, "capacity_w": feed_capacity_w(f)}


@router.patch("/feeds/{feed_id}")
async def update_feed(feed_id: str, body: FeedPatch, db: DB, principal: Editor) -> dict:
    f = await _get(db, PowerFeed, feed_id, "Power feed")
    fields = body.model_dump(exclude_unset=True)
    if fields.get("location_id"):
        await _get(db, Location, fields["location_id"], "Location")
    for k, v in fields.items():
        setattr(f, k, v)
    await audit.record(db, principal.actor, "power.feed.update", target_type="power_feed", target_id=f.id, detail=fields)
    return {"id": f.id, "name": f.name, "capacity_w": feed_capacity_w(f)}


@router.delete("/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(feed_id: str, db: DB, principal: Editor) -> None:
    f = await _get(db, PowerFeed, feed_id, "Power feed")
    await audit.record(db, principal.actor, "power.feed.delete", target_type="power_feed", target_id=f.id, detail={"name": f.name})
    await db.delete(f)


# -- graph ----------------------------------------------------------------------------


@dataclass
class Grid:
    feeds: dict[str, PowerFeed]
    items: dict[str, RackItem]
    links: list[PowerLink]
    racks: dict[str, Rack]

    def inputs(self, item_id: str) -> list[PowerLink]:
        return [ln for ln in self.links if ln.target_item_id == item_id]

    def outputs(self, source: str) -> list[PowerLink]:
        return [ln for ln in self.links if source in (ln.source_feed_id, ln.source_item_id)]

    def counts(self, item: RackItem, planned: bool) -> bool:
        return item.status == "installed" or (planned and item.status == "planned")

    def downstream(self, source: str, planned: bool) -> set[str]:
        """Items a source powers, directly or through UPS/PDU items."""
        seen: set[str] = set()
        todo = [source]
        while todo:
            for ln in self.outputs(todo.pop()):
                t = self.items.get(ln.target_item_id)
                if t is None or t.id in seen or not self.counts(t, planned):
                    continue
                seen.add(t.id)
                if t.kind in SOURCES:
                    todo.append(t.id)
        return seen

    def load(self, source: str, *, failover: bool, planned: bool) -> float:
        """Watts a source (feed or UPS/PDU item id) delivers.

        For each load with n live inputs, k of which reach this source: normal
        share is draw·k/n; with one input lost elsewhere it is draw·k/(n-1), or
        the full draw when every input reaches this source anyway.
        """
        below = self.downstream(source, planned)
        total = 0.0
        for item_id in below:
            item = self.items[item_id]
            draw = float(item.power_draw_w or 0)
            if not draw:
                continue
            live = [ln for ln in self.inputs(item_id) if self._active_source(ln, planned)]
            n = len(live) or 1
            k = sum(1 for ln in live if ln.source_feed_id == source or ln.source_item_id == source or ln.source_item_id in below)
            if not failover or n == 1:
                total += draw * k / n
            else:
                total += draw if k == n else draw * k / (n - 1)
        return total

    def _active_source(self, ln: PowerLink, planned: bool) -> bool:
        if ln.source_feed_id:
            return ln.source_feed_id in self.feeds
        src = self.items.get(ln.source_item_id or "")
        return src is not None and self.counts(src, planned)

    def capacity(self, source: str) -> float | None:
        if source in self.feeds:
            return feed_capacity_w(self.feeds[source])
        item = self.items.get(source)
        return float(item.power_capacity_w) if item and item.power_capacity_w else None

    def roots(self, item_id: str, _stack: frozenset[str] = frozenset()) -> set[str]:
        """Feeds an item ultimately draws from (empty if the chain is undocumented)."""
        out: set[str] = set()
        for ln in self.inputs(item_id):
            if ln.source_feed_id:
                out.add(ln.source_feed_id)
            elif ln.source_item_id and ln.source_item_id not in _stack:
                out |= self.roots(ln.source_item_id, _stack | {item_id})
        return out


async def load_grid(db: AsyncSession) -> Grid:
    return Grid(
        feeds={f.id: f for f in await db.scalars(select(PowerFeed))},
        items={i.id: i for i in await db.scalars(select(RackItem))},
        links=list(await db.scalars(select(PowerLink))),
        racks={r.id: r for r in await db.scalars(select(Rack))},
    )


def _pct(load: float, cap: float | None) -> float | None:
    return round(100 * load / cap, 1) if cap else None


def _source_view(grid: Grid, source: str) -> dict[str, Any]:
    cap = grid.capacity(source)
    normal = grid.load(source, failover=False, planned=False)
    failover = grid.load(source, failover=True, planned=False)
    projected = grid.load(source, failover=True, planned=True)
    view: dict[str, Any] = {
        "capacity_w": cap,
        "load_w": round(normal, 1),
        "failover_load_w": round(failover, 1),
        "projected_failover_w": round(projected, 1),
        "utilisation_pct": _pct(normal, cap),
        "failover_utilisation_pct": _pct(failover, cap),
        "projected_utilisation_pct": _pct(projected, cap),
        "headroom_w": round(cap - failover, 1) if cap else None,
    }
    item = grid.items.get(source)
    if item and item.kind == "ups" and item.runtime_full_load_min and cap and normal > 0:
        # Linear in load; real batteries usually last longer at partial load.
        view["runtime_estimate_min"] = round(item.runtime_full_load_min * cap / normal, 1)
    return view


def _node(grid: Grid, ln: PowerLink) -> dict[str, Any]:
    item = grid.items[ln.target_item_id]
    rack = grid.racks.get(item.rack_id)
    node: dict[str, Any] = {
        "link_id": ln.id,
        "outlet": ln.source_outlet,
        "input": ln.target_input,
        "item_id": item.id,
        "name": item.name,
        "kind": item.kind,
        "status": item.status,
        "rack": rack.name if rack else None,
        "draw_w": item.power_draw_w,
        "inputs": len(grid.inputs(item.id)),
        "is_demo": item.is_demo,
    }
    if item.kind in SOURCES:
        node |= _source_view(grid, item.id)
        node["children"] = [_node(grid, x) for x in grid.outputs(item.id) if x.target_item_id in grid.items]
    return node


@router.get("/chains")
async def chains(db: DB, principal: Reader) -> dict:
    """Power trees from each feed, plus UPS/PDU sources with no documented feed."""
    grid = await load_grid(db)
    trees = []
    for f in sorted(grid.feeds.values(), key=lambda f: f.name):
        trees.append(
            {
                "id": f.id,
                "kind": "feed",
                "name": f.name,
                "voltage_v": f.voltage_v,
                "amperage_a": f.amperage_a,
                "max_utilisation_pct": f.max_utilisation_pct,
                "description": f.description,
                "location_id": f.location_id,
                "is_demo": f.is_demo,
                **_source_view(grid, f.id),
                "children": [_node(grid, ln) for ln in grid.outputs(f.id) if ln.target_item_id in grid.items],
            }
        )
    orphans = [i for i in grid.items.values() if i.kind in SOURCES and i.status != "removed" and not grid.inputs(i.id)]
    for i in orphans:
        trees.append(
            {
                "id": i.id,
                "kind": i.kind,
                "name": i.name,
                "is_demo": i.is_demo,
                **_source_view(grid, i.id),
                "children": [_node(grid, ln) for ln in grid.outputs(i.id) if ln.target_item_id in grid.items],
            }
        )
    unpowered = [
        {"item_id": i.id, "name": i.name, "kind": i.kind, "rack": grid.racks[i.rack_id].name if i.rack_id in grid.racks else None}
        for i in grid.items.values()
        if i.status == "installed"
        and i.kind in ("device", "server", "ups", "pdu")
        and not grid.inputs(i.id)
        and not (i.kind in SOURCES and grid.outputs(i.id))
    ]
    return {"trees": trees, "unpowered": unpowered}


class LinkBody(BaseModel):
    source_feed_id: str | None = None
    source_item_id: str | None = None
    source_outlet: int | None = Field(default=None, ge=1, le=96)
    target_item_id: str
    target_input: str = Field(default="A", min_length=1, max_length=8)

    @model_validator(mode="after")
    def _one_source(self) -> LinkBody:
        if bool(self.source_feed_id) == bool(self.source_item_id):
            raise ValueError("Give exactly one of source_feed_id or source_item_id")
        return self


async def _validate_link(db: AsyncSession, body: LinkBody, exclude: str | None) -> None:
    grid = await load_grid(db)
    target = grid.items.get(body.target_item_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target item not found")
    if body.source_feed_id and body.source_feed_id not in grid.feeds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Power feed not found")
    if body.source_item_id:
        src = grid.items.get(body.source_item_id)
        if src is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Source item not found")
        if src.kind not in SOURCES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{src.name} is not a UPS or PDU")
        if body.source_outlet and src.outlet_count and body.source_outlet > src.outlet_count:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{src.name} has outlets 1–{src.outlet_count}")
        # The target must not already feed the source (directly or further up).
        upstream: set[str] = set()
        todo = [src.id]
        while todo:
            cur = todo.pop()
            if cur in upstream:
                continue
            upstream.add(cur)
            todo += [ln.source_item_id for ln in grid.inputs(cur) if ln.source_item_id]
        if target.id in upstream:
            raise HTTPException(status.HTTP_409_CONFLICT, "That link would create a loop in the power chain")
    for ln in grid.links:
        if ln.id == exclude:
            continue
        if body.source_outlet and ln.source_item_id == body.source_item_id and ln.source_outlet == body.source_outlet:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Outlet {body.source_outlet} is already in use")
        if ln.target_item_id == body.target_item_id and ln.target_input == body.target_input:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{target.name} input {body.target_input} is already connected")


@router.post("/links", status_code=status.HTTP_201_CREATED)
async def create_link(body: LinkBody, db: DB, principal: Editor) -> dict:
    await _validate_link(db, body, None)
    ln = PowerLink(**body.model_dump())
    db.add(ln)
    await db.flush()
    await audit.record(db, principal.actor, "power.link.create", target_type="power_link", target_id=ln.id, detail=body.model_dump())
    return {"id": ln.id, **body.model_dump()}


@router.put("/links/{link_id}")
async def update_link(link_id: str, body: LinkBody, db: DB, principal: Editor) -> dict:
    ln = await _get(db, PowerLink, link_id, "Power link")
    await _validate_link(db, body, ln.id)
    for k, v in body.model_dump().items():
        setattr(ln, k, v)
    await audit.record(db, principal.actor, "power.link.update", target_type="power_link", target_id=ln.id, detail=body.model_dump())
    return {"id": ln.id, **body.model_dump()}


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: str, db: DB, principal: Editor) -> None:
    ln = await _get(db, PowerLink, link_id, "Power link")
    await audit.record(db, principal.actor, "power.link.delete", target_type="power_link", target_id=ln.id)
    await db.delete(ln)


def _poe_max(poe: dict[str, Any]) -> float:
    return POE_MAX_W.get((poe.get("standard"), poe.get("type")), POE_BY_STANDARD.get(poe.get("standard") or "", 15.4))


@router.get("/poe")
async def poe(db: DB, principal: Reader) -> dict:
    """PoE ports delivering power per switch, with an upper bound from the negotiated standard."""
    devices = {d.id: d for d in await db.scalars(select(Device).where(Device.present.is_(True)))}
    per: dict[str, list[Port]] = defaultdict(list)
    for p in await db.scalars(select(Port).where(Port.device_id.in_(list(devices)))):
        if p.poe:
            per[p.device_id].append(p)
    out = []
    for did, ports in per.items():
        d = devices[did]
        active = [p for p in ports if (p.poe or {}).get("state") == "UP"]
        out.append(
            {
                "device_id": d.id,
                "name": d.name,
                "model": d.model,
                "poe_ports": len(ports),
                "active_ports": len(active),
                "upper_bound_w": round(sum(_poe_max(p.poe or {}) for p in active), 1),
                "ports": [
                    {
                        "idx": p.idx,
                        "standard": (p.poe or {}).get("standard"),
                        "type": (p.poe or {}).get("type"),
                        "state": (p.poe or {}).get("state"),
                    }
                    for p in sorted(ports, key=lambda p: p.idx)
                ],
                "is_demo": d.is_demo,
            }
        )
    return {
        "items": sorted(out, key=lambda x: x["name"]),
        "note": "The official API reports PoE state and standard but not wattage. "
        "Upper bound = maximum PSE power for each active port's standard.",
    }


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    grid = await load_grid(db)
    out: list[dict[str, Any]] = []
    sources = list(grid.feeds) + [i.id for i in grid.items.values() if i.kind in SOURCES and i.status != "removed"]
    for s in sources:
        name = grid.feeds[s].name if s in grid.feeds else grid.items[s].name
        v = _source_view(grid, s)
        if v["capacity_w"] is None:
            if grid.outputs(s):
                out.append(
                    {
                        "key": f"no_capacity:{s}",
                        "kind": "no_capacity",
                        "severity": "info",
                        "title": f"{name} has loads but no documented capacity",
                        "source_id": s,
                    }
                )
            continue
        for field, label, sev_over in (
            ("utilisation_pct", "now", "critical"),
            ("failover_utilisation_pct", "on failover", "warning"),
            ("projected_utilisation_pct", "with planned items", "info"),
        ):
            pct = v[field]
            if pct is None:
                continue
            if pct > 100:
                out.append(
                    {
                        "key": f"over:{field}:{s}",
                        "kind": "over_capacity",
                        "severity": sev_over,
                        "title": f"{name} is at {pct}% of usable capacity {label}",
                        "source_id": s,
                    }
                )
                break
            if pct >= WARN_PCT and field != "projected_utilisation_pct":
                out.append(
                    {
                        "key": f"near:{field}:{s}",
                        "kind": "near_capacity",
                        "severity": "warning",
                        "title": f"{name} is at {pct}% of usable capacity {label}",
                        "source_id": s,
                    }
                )
                break
        rt = v.get("runtime_estimate_min")
        if rt is not None and rt < LOW_RUNTIME_MIN:
            out.append(
                {
                    "key": f"runtime:{s}",
                    "kind": "low_runtime",
                    "severity": "warning",
                    "title": f"{name} runtime estimate is {rt} min at current load",
                    "source_id": s,
                }
            )
    for i in grid.items.values():
        if i.status != "installed":
            continue
        ins = grid.inputs(i.id)
        if len(ins) >= 2:
            roots = grid.roots(i.id)
            if len(roots) == 1:
                feed = grid.feeds[next(iter(roots))]
                out.append(
                    {
                        "key": f"shared_feed:{i.id}",
                        "kind": "shared_feed",
                        "severity": "warning",
                        "title": f"{i.name} has {len(ins)} power inputs but all trace back to {feed.name}",
                        "detail": "Redundant supplies only help if they reach different feeds.",
                        "item_id": i.id,
                    }
                )
        if i.kind in ("device", "server") and ins and i.power_draw_w is None:
            out.append(
                {
                    "key": f"no_draw:{i.id}",
                    "kind": "no_draw",
                    "severity": "info",
                    "title": f"{i.name} is powered but has no documented draw",
                    "item_id": i.id,
                }
            )
    return {"items": out}

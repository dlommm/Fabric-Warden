"""Cabling & fiber documentation: cables, patch panel pass-through, tracing, schedules.

A patch panel port passes straight through: front port N connects to rear
port N. Tracing follows cables and panels until it reaches something that is
not a panel. Cables are documentation; the controller does not report them.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.models import Cable, CableEnd, Device, Port, Rack, RackItem
from warden.security.rbac import P

router = APIRouter(prefix="/api/cabling", tags=["cabling"], dependencies=[Depends(require_module("cabling"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.DCIM_EDIT)

CableType = Literal["cat5e", "cat6", "cat6a", "cat8", "om3", "om4", "om5", "os2", "dac", "aoc", "coax", "power", "other"]
CableStatus = Literal["planned", "installed", "decommissioned"]
EndKind = Literal["device_port", "panel_port", "item", "external"]
Side = Literal["front", "rear"]
FIBER = {"om3", "om4", "om5", "os2", "aoc"}

Point = tuple[str, str, int | None, str | None]  # (kind, object id or text, port, side)


class EndIn(BaseModel):
    kind: EndKind
    device_id: str | None = None
    item_id: str | None = None
    port_idx: int | None = Field(default=None, ge=0, le=1024)
    side: Side | None = None
    text: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def _shape(self) -> EndIn:
        need = {
            "device_port": ("device_id", "port_idx"),
            "panel_port": ("item_id", "port_idx", "side"),
            "item": ("item_id",),
            "external": ("text",),
        }[self.kind]
        missing = [f for f in need if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"{self.kind} end needs {', '.join(missing)}")
        return self


class CableCreate(BaseModel):
    label: str = Field(default="", max_length=64)
    type: CableType = "cat6"
    color: str = Field(default="", max_length=32)
    length_m: float | None = Field(default=None, gt=0, le=10_000)
    status: CableStatus = "installed"
    description: str = Field(default="", max_length=2000)
    a: EndIn
    b: EndIn


class CablePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    type: CableType | None = None
    color: str | None = Field(default=None, max_length=32)
    length_m: float | None = Field(default=None, gt=0, le=10_000)
    status: CableStatus | None = None
    description: str | None = Field(default=None, max_length=2000)
    a: EndIn | None = None
    b: EndIn | None = None


# -- shared snapshot ------------------------------------------------------------------


@dataclass
class Plant:
    cables: dict[str, Cable]
    ends: dict[str, dict[str, CableEnd]]  # cable id → {"a": end, "b": end}
    devices: dict[str, Device]
    items: dict[str, RackItem]
    racks: dict[str, Rack]
    ports: dict[tuple[str, int], Port]

    def live(self, c: Cable) -> bool:
        return c.status != "decommissioned"

    def at(self, point: Point) -> tuple[Cable, str] | None:
        """The live cable terminating at a point, and which end of it."""
        for cid, pair in self.ends.items():
            c = self.cables[cid]
            if not self.live(c):
                continue
            for name, e in pair.items():
                if _point(e) == point:
                    return c, name
        return None

    def describe(self, e: CableEnd) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": e.kind, "port_idx": e.port_idx, "side": e.side, "text": e.text}
        if e.kind == "device_port":
            d = self.devices.get(e.device_id or "")
            port = self.ports.get((e.device_id or "", e.port_idx or 0))
            out |= {
                "device_id": e.device_id,
                "name": d.name if d else "(deleted device)",
                "port": {"state": port.state, "speed_mbps": port.speed_mbps, "connector": port.connector} if port else None,
                "is_demo": d.is_demo if d else False,
            }
        elif e.kind in ("panel_port", "item"):
            i = self.items.get(e.item_id or "")
            rack = self.racks.get(i.rack_id) if i else None
            out |= {"item_id": e.item_id, "name": i.name if i else "(deleted item)", "rack": rack.name if rack else None}
        else:
            out["name"] = e.text
        out["summary"] = _summary(out)
        return out


def _summary(d: dict[str, Any]) -> str:
    if d["kind"] == "device_port":
        # A cable accepted from an observed uplink has its ports still to confirm.
        return f"{d['name']} port {d['port_idx']}" if d["port_idx"] is not None else f"{d['name']} (port to confirm)"
    if d["kind"] == "panel_port":
        return f"{d['name']} port {d['port_idx']} ({d['side']})"
    return d["name"]


def _point(e: CableEnd | EndIn) -> Point:
    if e.kind == "device_port":
        return ("device_port", e.device_id or "", e.port_idx, None)
    if e.kind == "panel_port":
        return ("panel_port", e.item_id or "", e.port_idx, e.side)
    if e.kind == "item":
        return ("item", e.item_id or "", None, None)
    return ("external", e.text, None, None)


async def load_plant(db: AsyncSession) -> Plant:
    cables = {c.id: c for c in await db.scalars(select(Cable))}
    ends: dict[str, dict[str, CableEnd]] = {}
    for e in await db.scalars(select(CableEnd)):
        ends.setdefault(e.cable_id, {})[e.end] = e
    return Plant(
        cables=cables,
        ends=ends,
        devices={d.id: d for d in await db.scalars(select(Device))},
        items={i.id: i for i in await db.scalars(select(RackItem))},
        racks={r.id: r for r in await db.scalars(select(Rack))},
        ports={(p.device_id, p.idx): p for p in await db.scalars(select(Port))},
    )


def _cable(c: Cable, plant: Plant) -> dict[str, Any]:
    pair = plant.ends.get(c.id, {})
    return {
        "id": c.id,
        "label": c.label,
        "type": c.type,
        "medium": "fiber" if c.type in FIBER else "power" if c.type == "power" else "copper",
        "color": c.color,
        "length_m": c.length_m,
        "status": c.status,
        "description": c.description,
        "a": plant.describe(pair["a"]) if "a" in pair else None,
        "b": plant.describe(pair["b"]) if "b" in pair else None,
        "is_demo": c.is_demo,
    }


# -- validation -----------------------------------------------------------------------


async def _check_end(db: AsyncSession, end: EndIn, plant: Plant, exclude_cable: str | None) -> None:
    if end.kind == "device_port":
        if end.device_id not in plant.devices:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
        if (end.device_id, end.port_idx) not in plant.ports:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{plant.devices[end.device_id].name} has no port {end.port_idx}")
    elif end.kind in ("panel_port", "item"):
        item = plant.items.get(end.item_id or "")
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rack item not found")
        if end.kind == "panel_port":
            if item.kind != "patch_panel":
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{item.name} is not a patch panel")
            if not 1 <= (end.port_idx or 0) <= item.port_count:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{item.name} has ports 1–{item.port_count}")
    if end.kind in ("device_port", "panel_port"):
        taken = plant.at(_point(end))
        if taken and taken[0].id != exclude_cable:
            raise HTTPException(status.HTTP_409_CONFLICT, f"That port already has cable {taken[0].label}")


async def _next_label(db: AsyncSession) -> str:
    nums = [int(m.group(1)) for (label,) in (await db.execute(select(Cable.label))).all() if (m := re.fullmatch(r"C-(\d+)", label))]
    return f"C-{max(nums, default=0) + 1:04d}"


def _set_end(cable_id: str, name: str, body: EndIn, existing: CableEnd | None) -> CableEnd:
    e = existing or CableEnd(cable_id=cable_id, end=name)
    e.kind = body.kind
    e.device_id = body.device_id if body.kind == "device_port" else None
    e.item_id = body.item_id if body.kind in ("panel_port", "item") else None
    e.port_idx = body.port_idx if body.kind in ("device_port", "panel_port") else None
    e.side = body.side if body.kind == "panel_port" else None
    e.text = body.text
    return e


# -- routes ---------------------------------------------------------------------------


@router.get("/cables")
async def list_cables(db: DB, principal: Reader, q: str | None = None, status_: CableStatus | None = Query(None, alias="status")) -> dict:
    plant = await load_plant(db)
    rows = [_cable(c, plant) for c in sorted(plant.cables.values(), key=lambda c: c.label)]
    if status_:
        rows = [r for r in rows if r["status"] == status_]
    if q:
        t = q.lower()
        rows = [
            r
            for r in rows
            if t in r["label"].lower()
            or t in r["description"].lower()
            or any(t in (r[s] or {}).get("summary", "").lower() for s in ("a", "b"))
        ]
    return {"items": rows}


@router.post("/cables", status_code=status.HTTP_201_CREATED)
async def create_cable(body: CableCreate, db: DB, principal: Editor) -> dict:
    plant = await load_plant(db)
    if _point(body.a) == _point(body.b):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Both ends are the same point")
    for end in (body.a, body.b):
        await _check_end(db, end, plant, None)
    label = body.label.strip() or await _next_label(db)
    if await db.scalar(select(Cable).where(Cable.label == label)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Label {label} is already used")
    c = Cable(label=label, **body.model_dump(exclude={"label", "a", "b"}))
    db.add(c)
    await db.flush()
    plant.cables[c.id] = c
    plant.ends[c.id] = {"a": _set_end(c.id, "a", body.a, None), "b": _set_end(c.id, "b", body.b, None)}
    db.add_all(plant.ends[c.id].values())
    await db.flush()
    await audit.record(db, principal.actor, "cabling.cable.create", target_type="cable", target_id=c.id, detail={"label": c.label})
    return _cable(c, plant)


@router.patch("/cables/{cable_id}")
async def update_cable(cable_id: str, body: CablePatch, db: DB, principal: Editor) -> dict:
    plant = await load_plant(db)
    c = plant.cables.get(cable_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cable not found")
    fields = body.model_dump(exclude_unset=True, exclude={"a", "b"})
    if "label" in fields and await db.scalar(select(Cable).where(Cable.label == fields["label"], Cable.id != c.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Label {fields['label']} is already used")
    for name in ("a", "b"):
        end = getattr(body, name)
        if end is not None:
            await _check_end(db, end, plant, c.id)
            plant.ends[c.id][name] = _set_end(c.id, name, end, plant.ends[c.id].get(name))
    pair = plant.ends[c.id]
    if _point(pair["a"]) == _point(pair["b"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Both ends are the same point")
    for k, v in fields.items():
        setattr(c, k, v)
    changed = {**fields, **{n: getattr(body, n).model_dump() for n in ("a", "b") if getattr(body, n) is not None}}
    await audit.record(db, principal.actor, "cabling.cable.update", target_type="cable", target_id=c.id, detail=changed)
    return _cable(c, plant)


@router.delete("/cables/{cable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cable(cable_id: str, db: DB, principal: Editor) -> None:
    c = await db.get(Cable, cable_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cable not found")
    await audit.record(db, principal.actor, "cabling.cable.delete", target_type="cable", target_id=c.id, detail={"label": c.label})
    await db.delete(c)


def trace_from(plant: Plant, start: Point) -> list[dict[str, Any]]:
    """Follow cables and panel pass-throughs from a point until the path ends."""
    hops: list[dict[str, Any]] = []
    point = start
    seen: set[Point] = set()
    while point not in seen:
        seen.add(point)
        found = plant.at(point)
        if found is None:
            break
        cable, name = found
        pair = plant.ends[cable.id]
        near, far = pair[name], pair["b" if name == "a" else "a"]
        hops.append(
            {
                "cable": {"id": cable.id, "label": cable.label, "type": cable.type, "color": cable.color, "status": cable.status},
                "from": plant.describe(near),
                "to": plant.describe(far),
            }
        )
        if far.kind != "panel_port":
            break
        point = ("panel_port", far.item_id or "", far.port_idx, "rear" if far.side == "front" else "front")
    return hops


def _endpoints(hops: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return (hops[0]["from"], hops[-1]["to"]) if hops else (None, None)


@router.get("/trace")
async def trace(
    db: DB,
    principal: Reader,
    device_id: str | None = None,
    item_id: str | None = None,
    port_idx: int | None = None,
    side: Side | None = None,
) -> dict:
    plant = await load_plant(db)
    if device_id and port_idx is not None:
        start: Point = ("device_port", device_id, port_idx, None)
    elif item_id and port_idx is not None and side:
        start = ("panel_port", item_id, port_idx, side)
    elif item_id:
        start = ("item", item_id, None, None)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give device_id+port_idx, item_id+port_idx+side, or item_id")
    hops = trace_from(plant, start)
    # A panel port has two sides; show the whole path through it.
    if start[0] == "panel_port":
        back = trace_from(plant, ("panel_port", start[1], start[2], "rear" if start[3] == "front" else "front"))
        hops = [{"cable": h["cable"], "from": h["to"], "to": h["from"]} for h in reversed(back)] + hops
    a, b = _endpoints(hops)
    return {"hops": hops, "a": a, "b": b, "complete": bool(hops) and b is not None and b["kind"] != "panel_port"}


@router.get("/panels/{item_id}")
async def panel(item_id: str, db: DB, principal: Reader) -> dict:
    plant = await load_plant(db)
    item = plant.items.get(item_id)
    if item is None or item.kind != "patch_panel":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patch panel not found")
    ports = []
    for n in range(1, item.port_count + 1):
        row: dict[str, Any] = {"port": n}
        for side in ("front", "rear"):
            found = plant.at(("panel_port", item.id, n, side))
            if found:
                cable, name = found
                far = plant.ends[cable.id]["b" if name == "a" else "a"]
                row[side] = {"cable_id": cable.id, "label": cable.label, "status": cable.status, "far": plant.describe(far)}
            else:
                row[side] = None
        hops = trace_from(plant, ("panel_port", item.id, n, "front"))
        back = trace_from(plant, ("panel_port", item.id, n, "rear"))
        row["path"] = [h["from"]["summary"] for h in reversed(back)][-1:] + [h["to"]["summary"] for h in hops][-1:]
        ports.append(row)
    rack = plant.racks.get(item.rack_id)
    used = sum(1 for p in ports if p["front"] or p["rear"])
    return {
        "id": item.id,
        "name": item.name,
        "rack": rack.name if rack else None,
        "connector": item.connector,
        "ports": ports,
        "used": used,
    }


@router.get("/devices/{device_id}/ports")
async def device_ports(device_id: str, db: DB, principal: Reader) -> dict:
    """Documented cable and far end for each port of an observed device."""
    plant = await load_plant(db)
    if device_id not in plant.devices:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    out = []
    for (did, idx), _port in sorted(plant.ports.items()):
        if did != device_id:
            continue
        hops = trace_from(plant, ("device_port", device_id, idx, None))
        if hops:
            out.append({"port_idx": idx, "cable": hops[0]["cable"], "far": hops[-1]["to"], "hops": len(hops)})
    return {"items": out}


@router.get("/schedule.csv", response_class=PlainTextResponse)
async def schedule_csv(db: DB, principal: Reader) -> PlainTextResponse:
    plant = await load_plant(db)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["label", "type", "color", "length_m", "status", "a_end", "a_rack", "b_end", "b_rack", "description"])
    for c in sorted(plant.cables.values(), key=lambda c: c.label):
        row = _cable(c, plant)
        a, b = row["a"] or {}, row["b"] or {}
        w.writerow(
            [
                c.label,
                c.type,
                c.color,
                c.length_m or "",
                c.status,
                a.get("summary", ""),
                a.get("rack") or "",
                b.get("summary", ""),
                b.get("rack") or "",
                c.description,
            ]
        )
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="cable-schedule.csv"'}
    )


@router.get("/labels")
async def labels(
    db: DB, principal: Reader, ids: str = Query(default="", description="Comma-separated cable ids; empty = all live cables")
) -> dict:
    """Printable label text: one label per cable end, naming both ends."""
    plant = await load_plant(db)
    wanted = [i for i in ids.split(",") if i] or [c.id for c in plant.cables.values() if plant.live(c)]
    out = []
    for cid in wanted:
        c = plant.cables.get(cid)
        if c is None or not {"a", "b"} <= plant.ends.get(cid, {}).keys():
            continue
        a, b = (plant.describe(plant.ends[cid][n]) for n in ("a", "b"))
        out.append(
            {"cable_id": cid, "label": c.label, "lines": [f"{c.label} · {c.type.upper()}", f"A {a['summary']}", f"B {b['summary']}"]}
        )
    return {"items": sorted(out, key=lambda x: x["label"])}


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    """Documented cabling compared with what the controller reports."""
    return {"items": compute_findings(await load_plant(db))}


def compute_findings(plant: Plant) -> list[dict[str, Any]]:
    """Installed cables whose port reports link down, and documented device paths that are not an observed uplink."""
    by_ext = {d.external_id: d for d in plant.devices.values()}
    out: list[dict[str, Any]] = []
    seen_paths: set[frozenset[str]] = set()
    for c in plant.cables.values():
        if c.status != "installed":
            continue
        for e in plant.ends.get(c.id, {}).values():
            if e.kind == "device_port":
                port = plant.ports.get((e.device_id or "", e.port_idx or 0))
                d = plant.devices.get(e.device_id or "")
                if port and port.state == "DOWN" and d and d.present:
                    out.append(
                        {
                            "key": f"cabled_port_down:{c.id}:{e.end}",
                            "kind": "cabled_port_down",
                            "severity": "info",
                            "title": f"{c.label} is documented on {d.name} port {e.port_idx}, which reports link down",
                            "cable_id": c.id,
                        }
                    )
        # Device-to-device paths should match an observed uplink in one direction.
        pair = plant.ends.get(c.id, {})
        if "a" not in pair or pair["a"].kind != "device_port":
            continue
        hops = trace_from(plant, _point(pair["a"]))
        a, b = _endpoints(hops)
        if not (a and b and b["kind"] == "device_port"):
            continue
        key = frozenset({a["device_id"], b["device_id"]})
        if key in seen_paths or len(key) < 2:
            continue
        seen_paths.add(key)
        da, db_ = plant.devices.get(a["device_id"]), plant.devices.get(b["device_id"])
        if not (da and db_ and da.present and db_.present):
            continue
        up_a, up_b = by_ext.get(da.uplink_device_external_id or ""), by_ext.get(db_.uplink_device_external_id or "")
        if up_a is not db_ and up_b is not da:
            out.append(
                {
                    "key": f"path_not_observed:{':'.join(sorted(key))}",
                    "kind": "path_not_observed",
                    "severity": "warning",
                    "title": f"Documented path {da.name} ↔ {db_.name} is not an observed uplink",
                    "detail": "Neither device reports the other as its uplink. It may be a secondary or aggregated link, "
                    "or the documentation is out of date.",
                    "cable_id": c.id,
                }
            )
    return out


def _documented_pairs(plant: Plant) -> set[frozenset[str]]:
    """Device pairs joined end to end by a live (planned or installed) cable path."""
    pairs: set[frozenset[str]] = set()
    for cid, ends in plant.ends.items():
        c = plant.cables.get(cid)
        if c is None or not plant.live(c):
            continue
        for e in ends.values():
            if e.kind != "device_port":
                continue
            a, b = _endpoints(trace_from(plant, _point(e)))
            if a and b and a.get("kind") == b.get("kind") == "device_port" and a.get("device_id") and b.get("device_id"):
                pairs.add(frozenset({a["device_id"], b["device_id"]}))
    return pairs


def undocumented_uplinks(plant: Plant) -> list[dict[str, Any]]:
    """Observed uplinks (device → the device it reports as its uplink) with no documented cable between them.

    The official API names the uplink device but not the port on either side, so these can only become
    planned cables with their ports left to confirm.
    """
    by_ext = {d.external_id: d for d in plant.devices.values()}
    documented = _documented_pairs(plant)
    out: list[dict[str, Any]] = []
    for d in sorted(plant.devices.values(), key=lambda d: d.name):
        up = by_ext.get(d.uplink_device_external_id or "")
        if not (d.present and up and up.present and up.id != d.id):
            continue
        if frozenset({d.id, up.id}) in documented:
            continue
        out.append(
            {
                "key": f"uplink_undocumented:{d.id}:{up.id}",
                "kind": "uplink_undocumented",
                "severity": "info",
                "title": f"{d.name} uplinks to {up.name}, but no cable is documented between them",
                "device": {"id": d.id, "name": d.name, "model": d.model},
                "uplink": {"id": up.id, "name": up.name, "model": up.model},
                "is_demo": d.is_demo,
            }
        )
    return out


class AcceptSuggestions(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)
    type: CableType = "cat6"


@router.get("/suggestions")
async def suggestions(db: DB, principal: Reader) -> dict:
    """Cables the observed uplinks imply but nobody has documented yet."""
    return {"items": undocumented_uplinks(await load_plant(db))}


@router.post("/suggestions/accept", status_code=status.HTTP_201_CREATED)
async def accept_suggestions(body: AcceptSuggestions, db: DB, principal: Editor) -> dict:
    """Document suggested uplinks as planned cables, numbered in order, with both ports still to confirm."""
    plant = await load_plant(db)
    open_ = {s["key"]: s for s in undocumented_uplinks(plant)}
    missing = [k for k in body.keys if k not in open_]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"No longer suggested: {', '.join(missing)}")
    created = []
    for key in dict.fromkeys(body.keys):
        s = open_[key]
        label = await _next_label(db)
        c = Cable(
            label=label,
            type=body.type,
            status="planned",
            description=f"From the observed uplink {s['device']['name']} → {s['uplink']['name']}; confirm both ports.",
        )
        db.add(c)
        await db.flush()
        ends = {
            "a": CableEnd(cable_id=c.id, end="a", kind="device_port", device_id=s["device"]["id"], port_idx=None, text=""),
            "b": CableEnd(cable_id=c.id, end="b", kind="device_port", device_id=s["uplink"]["id"], port_idx=None, text=""),
        }
        db.add_all(ends.values())
        await db.flush()
        plant.cables[c.id] = c
        plant.ends[c.id] = ends
        detail = {"label": c.label, "from_suggestion": key}
        await audit.record(db, principal.actor, "cabling.cable.create", target_type="cable", target_id=c.id, detail=detail)
        created.append(_cable(c, plant))
    return {"items": created}

"""Asset lifecycle: hardware, spares, licences and subscriptions, with expiry tracking.

Device asset records from Phase 2 (tag, serial, warranty on an observed
device) are included in the renewal calendar and findings as read-only rows
with source="device"; they are still edited on the device.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.models import Asset, Device, Location, RackItem
from warden.security.rbac import P

router = APIRouter(prefix="/api/assets", tags=["assets"], dependencies=[Depends(require_module("asset-lifecycle"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.DCIM_EDIT)

Kind = Literal["hardware", "spare", "licence", "subscription", "consumable"]
Status = Literal["ordered", "in_service", "spare", "in_repair", "retired", "disposed"]
INACTIVE = {"retired", "disposed"}
# Past-tense wording for findings: "warranty ended 30 days ago", not "warranty ends passed".
PAST = {"warranty_until": "warranty ended", "support_until": "support ended", "expires_on": "expired", "end_of_life": "reached end of life"}
SOON_DAYS = 90
# (field, what it means) in the order a renewal calendar reads them.
DATES = (
    ("warranty_until", "Warranty ends"),
    ("support_until", "Support ends"),
    ("expires_on", "Expires"),
    ("end_of_sale", "End of sale"),
    ("end_of_life", "End of life"),
)


class AssetBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Kind = "hardware"
    status: Status = "in_service"
    asset_tag: str = Field(default="", max_length=64)
    vendor: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=64)
    serial: str = Field(default="", max_length=64)
    quantity: int = Field(default=1, ge=0, le=100_000)
    min_quantity: int | None = Field(default=None, ge=0, le=100_000)
    supplier: str = Field(default="", max_length=128)
    order_ref: str = Field(default="", max_length=64)
    purchase_date: date | None = None
    purchase_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="", max_length=3, pattern=r"^([A-Z]{3})?$")
    warranty_until: date | None = None
    support_until: date | None = None
    end_of_sale: date | None = None
    end_of_life: date | None = None
    seats: int | None = Field(default=None, ge=0, le=1_000_000)
    expires_on: date | None = None
    auto_renew: bool = False
    device_id: str | None = None
    rack_item_id: str | None = None
    location_id: str | None = None
    labels: list[str] = Field(default_factory=list, max_length=32)
    notes: str = Field(default="", max_length=20_000)


class AssetPatch(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Kind | None = None
    status: Status | None = None
    asset_tag: str | None = Field(default=None, max_length=64)
    vendor: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    serial: str | None = Field(default=None, max_length=64)
    quantity: int | None = Field(default=None, ge=0, le=100_000)
    min_quantity: int | None = Field(default=None, ge=0, le=100_000)
    supplier: str | None = Field(default=None, max_length=128)
    order_ref: str | None = Field(default=None, max_length=64)
    purchase_date: date | None = None
    purchase_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, max_length=3, pattern=r"^([A-Z]{3})?$")
    warranty_until: date | None = None
    support_until: date | None = None
    end_of_sale: date | None = None
    end_of_life: date | None = None
    seats: int | None = Field(default=None, ge=0, le=1_000_000)
    expires_on: date | None = None
    auto_renew: bool | None = None
    device_id: str | None = None
    rack_item_id: str | None = None
    location_id: str | None = None
    labels: list[str] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=20_000)


async def _check_links(db: AsyncSession, fields: dict[str, Any]) -> None:
    for key, model, what in (
        ("device_id", Device, "Device"),
        ("rack_item_id", RackItem, "Rack item"),
        ("location_id", Location, "Location"),
    ):
        if fields.get(key) and await db.get(model, fields[key]) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def _out(a: Asset, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "kind": a.kind,
        "status": a.status,
        "asset_tag": a.asset_tag,
        "vendor": a.vendor,
        "model": a.model,
        "serial": a.serial,
        "quantity": a.quantity,
        "min_quantity": a.min_quantity,
        "supplier": a.supplier,
        "order_ref": a.order_ref,
        "purchase_date": a.purchase_date,
        "purchase_cost": str(a.purchase_cost) if a.purchase_cost is not None else None,
        "currency": a.currency,
        "warranty_until": a.warranty_until,
        "support_until": a.support_until,
        "end_of_sale": a.end_of_sale,
        "end_of_life": a.end_of_life,
        "seats": a.seats,
        "expires_on": a.expires_on,
        "auto_renew": a.auto_renew,
        "device_id": a.device_id,
        "device": names.get(a.device_id or ""),
        "rack_item_id": a.rack_item_id,
        "rack_item": names.get(a.rack_item_id or ""),
        "location_id": a.location_id,
        "location": names.get(a.location_id or ""),
        "labels": a.labels or [],
        "notes": a.notes,
        "next_date": _next(a, date.today()),
        "is_demo": a.is_demo,
    }


def _next(a: Asset, today: date) -> dict[str, Any] | None:
    """The soonest lifecycle date that has not passed yet."""
    upcoming = [(getattr(a, f), label) for f, label in DATES if getattr(a, f) and getattr(a, f) >= today]
    if not upcoming:
        return None
    d, label = min(upcoming)
    return {"date": d, "label": label, "days": (d - today).days}


async def _names(db: AsyncSession) -> dict[str, str]:
    out = {d.id: d.name for d in await db.scalars(select(Device))}
    out |= {i.id: i.name for i in await db.scalars(select(RackItem))}
    out |= {loc.id: loc.name for loc in await db.scalars(select(Location))}
    return out


@router.get("")
async def list_assets(
    db: DB,
    principal: Reader,
    q: str | None = None,
    kind: Kind | None = None,
    status_: Status | None = Query(None, alias="status"),
    device_id: str | None = None,
) -> dict:
    stmt = select(Asset).order_by(Asset.name)
    if kind:
        stmt = stmt.where(Asset.kind == kind)
    if status_:
        stmt = stmt.where(Asset.status == status_)
    if device_id:
        stmt = stmt.where(Asset.device_id == device_id)
    rows = list(await db.scalars(stmt))
    if q:
        t = q.lower()
        rows = [
            a
            for a in rows
            if any(t in (v or "").lower() for v in (a.name, a.asset_tag, a.serial, a.vendor, a.model, a.supplier, a.order_ref))
        ]
    names = await _names(db)
    return {"items": [_out(a, names) for a in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_asset(body: AssetBody, db: DB, principal: Editor) -> dict:
    await _check_links(db, body.model_dump())
    a = Asset(**body.model_dump())
    db.add(a)
    await db.flush()
    await audit.record(db, principal.actor, "assets.create", target_type="asset", target_id=a.id, detail={"name": a.name, "kind": a.kind})
    return _out(a, await _names(db))


@router.get("/calendar")
async def calendar(db: DB, principal: Reader, days: int = Query(365, ge=1, le=3650), include_past: bool = True) -> dict:
    """Every lifecycle date within the window (and overdue ones), soonest first."""
    today = date.today()
    horizon = today + timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for a in await db.scalars(select(Asset).where(Asset.status.not_in(INACTIVE))):
        for f, label in DATES:
            d = getattr(a, f)
            if d and d <= horizon and (include_past or d >= today):
                rows.append(
                    {
                        "source": "asset",
                        "id": a.id,
                        "name": a.name,
                        "kind": a.kind,
                        "field": f,
                        "label": label,
                        "date": d,
                        "days": (d - today).days,
                        "auto_renew": a.auto_renew,
                        "is_demo": a.is_demo,
                    }
                )
    for dev in await db.scalars(select(Device).where(Device.present.is_(True))):
        raw = (dev.asset or {}).get("warranty_until")
        if not raw:
            continue
        d = date.fromisoformat(raw)
        if d <= horizon and (include_past or d >= today):
            rows.append(
                {
                    "source": "device",
                    "id": dev.id,
                    "name": dev.name,
                    "kind": "hardware",
                    "field": "warranty_until",
                    "label": "Warranty ends",
                    "date": d,
                    "days": (d - today).days,
                    "auto_renew": False,
                    "is_demo": dev.is_demo,
                }
            )
    return {"items": sorted(rows, key=lambda r: r["date"])}


@router.get("/summary")
async def summary(db: DB, principal: Reader) -> dict:
    rows = list(await db.scalars(select(Asset)))
    today = date.today()
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    spend: dict[str, Decimal] = {}
    for a in rows:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        by_status[a.status] = by_status.get(a.status, 0) + 1
        if a.purchase_cost is not None and a.status not in INACTIVE:
            cur = a.currency or "—"
            spend[cur] = spend.get(cur, Decimal(0)) + a.purchase_cost * max(a.quantity, 1)
    active = [a for a in rows if a.status not in INACTIVE]
    soon = sum(1 for a in active if (n := _next(a, today)) and n["days"] <= SOON_DAYS)
    return {
        "total": len(rows),
        "active": len(active),
        "by_kind": by_kind,
        "by_status": by_status,
        "spend": {k: str(v) for k, v in spend.items()},
        "due_within_90_days": soon,
    }


@router.get("/export.csv", response_class=PlainTextResponse)
async def export_csv(db: DB, principal: Reader) -> PlainTextResponse:
    names = await _names(db)
    cols = [
        "name",
        "kind",
        "status",
        "asset_tag",
        "vendor",
        "model",
        "serial",
        "quantity",
        "supplier",
        "order_ref",
        "purchase_date",
        "purchase_cost",
        "currency",
        "warranty_until",
        "support_until",
        "end_of_sale",
        "end_of_life",
        "seats",
        "expires_on",
        "auto_renew",
        "device",
        "rack_item",
        "location",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for a in await db.scalars(select(Asset).order_by(Asset.name)):
        row = _out(a, names)
        w.writerow(["" if row[c] is None else row[c] for c in cols])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="assets.csv"'})


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    today = date.today()
    out: list[dict[str, Any]] = []
    for a in await db.scalars(select(Asset).where(Asset.status.not_in(INACTIVE))):
        for f, label in DATES:
            d = getattr(a, f)
            if d is None or f == "end_of_sale":
                continue
            days = (d - today).days
            if days < 0:
                sev = "info" if a.auto_renew else "warning"
                out.append(
                    {
                        "key": f"{f}:{a.id}",
                        "kind": "expired",
                        "severity": sev,
                        "title": f"{a.name}: {PAST[f]} {-days} days ago",
                        "asset_id": a.id,
                        "date": d,
                    }
                )
            elif days <= SOON_DAYS and not a.auto_renew:
                out.append(
                    {
                        "key": f"{f}:{a.id}",
                        "kind": "expiring",
                        "severity": "info",
                        "title": f"{a.name}: {label.lower()} in {days} days",
                        "asset_id": a.id,
                        "date": d,
                    }
                )
        if a.min_quantity is not None and a.quantity < a.min_quantity:
            out.append(
                {
                    "key": f"low_stock:{a.id}",
                    "kind": "low_stock",
                    "severity": "warning",
                    "title": f"{a.name}: {a.quantity} on hand, minimum {a.min_quantity}",
                    "asset_id": a.id,
                }
            )
    for dev in await db.scalars(select(Device).where(Device.present.is_(True))):
        raw = (dev.asset or {}).get("warranty_until")
        if raw and (days := (date.fromisoformat(raw) - today).days) <= SOON_DAYS:
            title = f"{dev.name}: warranty {'passed ' + str(-days) + ' days ago' if days < 0 else 'ends in ' + str(days) + ' days'}"
            out.append(
                {
                    "key": f"device_warranty:{dev.id}",
                    "kind": "expired" if days < 0 else "expiring",
                    "severity": "info",
                    "title": title,
                    "device_id": dev.id,
                }
            )
    return {"items": sorted(out, key=lambda f: (f["severity"] != "warning", f["title"]))}


@router.get("/{asset_id}")
async def get_asset(asset_id: str, db: DB, principal: Reader) -> dict:
    a = await db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    return _out(a, await _names(db))


@router.patch("/{asset_id}")
async def update_asset(asset_id: str, body: AssetPatch, db: DB, principal: Editor) -> dict:
    a = await db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    fields = body.model_dump(exclude_unset=True)
    await _check_links(db, fields)
    for k, v in fields.items():
        setattr(a, k, v)
    await audit.record(
        db, principal.actor, "assets.update", target_type="asset", target_id=a.id, detail=body.model_dump(mode="json", exclude_unset=True)
    )
    return _out(a, await _names(db))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: str, db: DB, principal: Editor) -> None:
    a = await db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    await audit.record(db, principal.actor, "assets.delete", target_type="asset", target_id=a.id, detail={"name": a.name})
    await db.delete(a)

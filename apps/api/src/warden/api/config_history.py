"""Configuration History & Backup: snapshots per site, semantic diffs, baseline and drift."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from warden import audit
from warden import config_history as ch
from warden.api.deps import DB, Allowed, require_module
from warden.models import ConfigSnapshot, Site
from warden.security.rbac import P

router = APIRouter(prefix="/api/config", tags=["config-history"], dependencies=[Depends(require_module("config-history"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.INVENTORY_EDIT)


def _snap(s: ConfigSnapshot, site: Site | None = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "site_id": s.site_id,
        "site": site.name if site else None,
        "taken_at": s.taken_at,
        "hash": s.content_hash[:12],
        "summary": s.summary,
        "changes": s.change_counts,
        "change_total": sum(sum(v.values()) for v in (s.change_counts or {}).values()),
        "trigger": s.trigger,
        "source": s.source,
        "is_baseline": s.is_baseline,
        "note": s.note,
        "is_demo": s.is_demo,
    }


@router.get("/snapshots")
async def list_snapshots(db: DB, principal: Reader, site_id: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    sites = {s.id: s for s in await db.scalars(select(Site))}
    stmt = select(ConfigSnapshot).order_by(ConfigSnapshot.taken_at.desc()).limit(limit)
    if site_id:
        stmt = stmt.where(ConfigSnapshot.site_id == site_id)
    return {
        "items": [_snap(s, sites.get(s.site_id)) for s in await db.scalars(stmt)],
        "sites": [{"id": s.id, "name": s.name} for s in sites.values() if s.present],
    }


async def _get(db: DB, sid: str) -> ConfigSnapshot:
    s = await db.get(ConfigSnapshot, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Snapshot not found")
    return s


@router.get("/snapshots/{sid}")
async def get_snapshot(sid: str, db: DB, principal: Reader) -> dict:
    s = await _get(db, sid)
    prev = await db.scalar(
        select(ConfigSnapshot)
        .where(ConfigSnapshot.site_id == s.site_id, ConfigSnapshot.taken_at < s.taken_at)
        .order_by(ConfigSnapshot.taken_at.desc())
        .limit(1)
    )
    return {
        **_snap(s, await db.get(Site, s.site_id)),
        "content": s.content,
        "previous_id": prev.id if prev else None,
        "diff": ch.diff(prev.content, s.content) if prev else {},
    }


@router.get("/diff")
async def diff(db: DB, principal: Reader, from_: str = Query(alias="from"), to: str = Query()) -> dict:
    a, b = await _get(db, from_), await _get(db, to)
    if a.site_id != b.site_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Both snapshots must belong to the same site")
    d = ch.diff(a.content, b.content)
    return {"from": _snap(a), "to": _snap(b), "diff": d, "counts": ch.counts(d), "labels": ch.SECTION_LABEL}


@router.get("/drift")
async def drift(db: DB, principal: Reader) -> dict:
    """Each site's latest configuration against its pinned baseline."""
    out = []
    for site in await db.scalars(select(Site).where(Site.present.is_(True))):
        base = await db.scalar(
            select(ConfigSnapshot)
            .where(ConfigSnapshot.site_id == site.id, ConfigSnapshot.is_baseline.is_(True))
            .order_by(ConfigSnapshot.taken_at.desc())
            .limit(1)
        )
        last = await db.scalar(
            select(ConfigSnapshot).where(ConfigSnapshot.site_id == site.id).order_by(ConfigSnapshot.taken_at.desc()).limit(1)
        )
        if not last:
            continue
        d = ch.diff(base.content, last.content) if base else {}
        out.append(
            {
                "site": {"id": site.id, "name": site.name, "is_demo": site.is_demo},
                "baseline": _snap(base) if base else None,
                "latest": _snap(last),
                "diff": d,
                "counts": ch.counts(d),
            }
        )
    return {"items": out, "labels": ch.SECTION_LABEL}


class Pin(BaseModel):
    note: str = Field(default="", max_length=500)


@router.post("/snapshots/{sid}/baseline")
async def pin_baseline(sid: str, body: Pin, db: DB, principal: Editor) -> dict:
    s = await _get(db, sid)
    await db.execute(update(ConfigSnapshot).where(ConfigSnapshot.site_id == s.site_id).values(is_baseline=False))
    s.is_baseline = True
    if body.note:
        s.note = body.note
    await audit.record(
        db,
        principal.actor,
        "config.baseline.pin",
        target_type="config_snapshot",
        target_id=s.id,
        detail={"site_id": s.site_id, "note": body.note},
    )
    return _snap(s)


@router.post("/capture", status_code=status.HTTP_201_CREATED)
async def capture_now(db: DB, principal: Editor, site_id: str = Query()) -> dict:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    s = await ch.capture(db, site, trigger="manual", source="manual")
    assert s is not None
    await audit.record(
        db, principal.actor, "config.snapshot.capture", target_type="config_snapshot", target_id=s.id, detail={"site_id": site.id}
    )
    return _snap(s, site)

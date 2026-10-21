"""Documentation & wiki: versioned Markdown pages with live inventory references.

A reference is written `[[type:key]]` or `[[type:key|text]]`, for example
`[[device:Core Aggregation]]` or `[[prefix:10.0.30.0/24|server subnet]]`.
References are resolved on every read, so a page shows the object's current
state and a renamed or removed object shows up as unresolved instead of
silently pointing at stale text. Every save appends a version; saves carry
the version they were based on and are rejected if someone saved in between.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, require_module
from warden.models import Asset, Cable, Client, Device, DocPage, DocVersion, IpPrefix, Network, Rack, VlanRecord
from warden.security.rbac import P

router = APIRouter(prefix="/api/wiki", tags=["docs"], dependencies=[Depends(require_module("docs"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.DOCS_EDIT)

REF_RE = re.compile(r"\[\[(device|client|network|vlan|prefix|rack|cable|asset|page):([^\]|]+?)(?:\|([^\]]+))?\]\]")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REF_TYPES = ("device", "client", "network", "vlan", "prefix", "rack", "cable", "asset", "page")


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:100] or "page"


# -- references -----------------------------------------------------------------------


async def _resolve(db: AsyncSession, kind: str, key: str) -> dict[str, Any] | None:
    k = key.strip()
    low = k.lower()
    if kind == "device":
        d = await db.scalar(
            select(Device)
            .where(or_(Device.id == k, func.lower(Device.name) == low, Device.mac_address == low))
            .order_by(Device.present.desc())
        )
        if d:
            return {
                "id": d.id,
                "label": d.name,
                "href": f"/inventory/devices/{d.id}",
                "state": d.state if d.present else "MISSING",
                "detail": f"{d.model} · {d.ip_address or d.mac_address}",
                "is_demo": d.is_demo,
            }
    elif kind == "client":
        c = await db.scalar(
            select(Client)
            .where(
                or_(
                    Client.id == k,
                    func.lower(Client.name) == low,
                    func.lower(Client.alias) == low,
                    Client.mac_address == low,
                    Client.ip_address == k,
                )
            )
            .order_by(Client.present.desc())
        )
        if c:
            return {
                "id": c.id,
                "label": c.alias or c.name,
                "href": f"/inventory/clients/{c.id}",
                "state": "ONLINE" if c.present else "OFFLINE",
                "detail": c.ip_address or c.mac_address or "",
                "is_demo": c.is_demo,
            }
    elif kind == "network":
        n = await db.scalar(select(Network).where(or_(Network.id == k, func.lower(Network.name) == low)).order_by(Network.present.desc()))
        if n:
            return {
                "id": n.id,
                "label": n.name,
                "href": "/inventory/networks",
                "state": "ENABLED" if n.enabled and n.present else "DISABLED",
                "detail": f"VLAN {n.vlan_id}" if n.vlan_id else n.management,
                "is_demo": n.is_demo,
            }
    elif kind == "vlan":
        try:
            vid = int(k)
        except ValueError:
            return None
        v = await db.scalar(select(VlanRecord).where(VlanRecord.vlan_id == vid))
        n = await db.scalar(select(Network).where(Network.vlan_id == vid, Network.present.is_(True)))
        if v or n:
            return {
                "id": (v or n).id,
                "label": f"VLAN {vid}",
                "href": "/ipam/vlans",
                "state": (v.status.upper() if v else "OBSERVED"),
                "detail": (v.name if v else n.name),
                "is_demo": (v or n).is_demo,
            }  # type: ignore[union-attr]
    elif kind == "prefix":
        p = await db.scalar(select(IpPrefix).where(or_(IpPrefix.id == k, IpPrefix.cidr == k)))
        if p:
            return {
                "id": p.id,
                "label": p.cidr,
                "href": f"/ipam/prefixes/{p.id}",
                "state": p.status.upper(),
                "detail": p.description,
                "is_demo": p.is_demo,
            }
    elif kind == "rack":
        r = await db.scalar(select(Rack).where(or_(Rack.id == k, func.lower(Rack.name) == low)))
        if r:
            return {
                "id": r.id,
                "label": r.name,
                "href": f"/dcim/racks/{r.id}",
                "state": r.status.upper(),
                "detail": f"{r.u_height} U",
                "is_demo": r.is_demo,
            }
    elif kind == "cable":
        c = await db.scalar(select(Cable).where(or_(Cable.id == k, func.lower(Cable.label) == low)))
        if c:
            return {
                "id": c.id,
                "label": c.label,
                "href": f"/dcim/cables?q={c.label}",
                "state": c.status.upper(),
                "detail": c.type,
                "is_demo": c.is_demo,
            }
    elif kind == "asset":
        a = await db.scalar(select(Asset).where(or_(Asset.id == k, func.lower(Asset.asset_tag) == low, func.lower(Asset.name) == low)))
        if a:
            return {
                "id": a.id,
                "label": a.name,
                "href": f"/assets/{a.id}",
                "state": a.status.upper(),
                "detail": a.asset_tag or a.model,
                "is_demo": a.is_demo,
            }
    elif kind == "page":
        pg = await db.scalar(select(DocPage).where(or_(DocPage.slug == low, func.lower(DocPage.title) == low)))
        if pg:
            return {
                "id": pg.id,
                "label": pg.title,
                "href": f"/docs/{pg.slug}",
                "state": None,
                "detail": f"v{pg.version}",
                "is_demo": pg.is_demo,
            }
    return None


async def resolve_refs(db: AsyncSession, body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    for m in REF_RE.finditer(body):
        kind, key, text = m.group(1), m.group(2).strip(), m.group(3)
        if (kind, key) not in cache:
            cache[(kind, key)] = await _resolve(db, kind, key)
        hit = cache[(kind, key)]
        out.append({"raw": m.group(0), "type": kind, "key": key, "text": text, "resolved": hit is not None, **(hit or {})})
    return out


# -- pages ----------------------------------------------------------------------------


def _summary(p: DocPage) -> dict[str, Any]:
    return {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "parent_id": p.parent_id,
        "tags": p.tags or [],
        "version": p.version,
        "updated_by": p.updated_by,
        "updated_at": p.updated_at,
        "is_demo": p.is_demo,
    }


class PageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=128)
    parent_id: str | None = None
    body: str = Field(default="", max_length=500_000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    message: str = Field(default="", max_length=200)


class PageSave(BaseModel):
    base_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    body: str = Field(max_length=500_000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    message: str = Field(default="", max_length=200)


async def _page(db: AsyncSession, slug: str) -> DocPage:
    p = await db.scalar(select(DocPage).where(DocPage.slug == slug))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return p


async def _check_parent(db: AsyncSession, page_id: str | None, parent_id: str | None) -> None:
    cur, seen = parent_id, set()
    while cur:
        if cur == page_id or cur in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A page cannot be nested inside itself")
        seen.add(cur)
        parent = await db.get(DocPage, cur)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent page not found")
        cur = parent.parent_id


@router.get("/pages")
async def list_pages(db: DB, principal: Reader, q: str | None = None, tag: str | None = None) -> dict:
    pages = list(await db.scalars(select(DocPage).order_by(DocPage.title)))
    if tag:
        pages = [p for p in pages if tag in (p.tags or [])]
    if q:
        t = q.lower()
        pages = [p for p in pages if t in p.title.lower() or t in p.body.lower() or any(t in x.lower() for x in p.tags or [])]
    return {"items": [_summary(p) for p in pages], "tags": sorted({t for p in pages for t in p.tags or []})}


@router.post("/pages", status_code=status.HTTP_201_CREATED)
async def create_page(body: PageCreate, db: DB, principal: Editor) -> dict:
    slug = (body.slug or "").strip().lower() or slugify(body.title)
    if not SLUG_RE.match(slug):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Slug may contain lowercase letters, digits and single dashes")
    base, n = slug, 2
    while await db.scalar(select(DocPage).where(DocPage.slug == slug)):
        if body.slug:
            raise HTTPException(status.HTTP_409_CONFLICT, f"A page with slug {slug} already exists")
        slug, n = f"{base}-{n}", n + 1
    await _check_parent(db, None, body.parent_id)
    who = principal.user.username
    p = DocPage(slug=slug, title=body.title, parent_id=body.parent_id, body=body.body, tags=body.tags, version=1, updated_by=who)
    db.add(p)
    await db.flush()
    db.add(DocVersion(page_id=p.id, version=1, title=p.title, body=p.body, message=body.message or "Created", author=who))
    await audit.record(db, principal.actor, "docs.page.create", target_type="doc_page", target_id=p.id, detail={"slug": slug})
    return _summary(p)


@router.get("/pages/{slug}")
async def get_page(slug: str, db: DB, principal: Reader) -> dict:
    p = await _page(db, slug)
    children = await db.scalars(select(DocPage).where(DocPage.parent_id == p.id).order_by(DocPage.title))
    parent = await db.get(DocPage, p.parent_id) if p.parent_id else None
    backlinks = await _backlinks(db, "page", [p.slug, p.title.lower(), p.id], exclude=p.id)
    return {
        **_summary(p),
        "body": p.body,
        "references": await resolve_refs(db, p.body),
        "children": [_summary(c) for c in children],
        "parent": _summary(parent) if parent else None,
        "backlinks": backlinks,
    }


@router.put("/pages/{slug}")
async def save_page(slug: str, body: PageSave, db: DB, principal: Editor) -> dict:
    p = await _page(db, slug)
    if body.base_version != p.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This page was saved by {p.updated_by} (version {p.version}) after you opened version {body.base_version}. "
            "Reload and reapply your edits.",
        )
    await _check_parent(db, p.id, body.parent_id)
    if (body.title, body.body, body.tags, body.parent_id) == (p.title, p.body, p.tags or [], p.parent_id):
        return _summary(p)
    who = principal.user.username
    p.title, p.body, p.tags, p.parent_id = body.title, body.body, body.tags, body.parent_id
    p.version += 1
    p.updated_by = who
    db.add(DocVersion(page_id=p.id, version=p.version, title=p.title, body=p.body, message=body.message, author=who))
    await audit.record(
        db, principal.actor, "docs.page.save", target_type="doc_page", target_id=p.id, detail={"slug": p.slug, "version": p.version}
    )
    return _summary(p)


@router.delete("/pages/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(slug: str, db: DB, principal: Editor) -> None:
    p = await _page(db, slug)
    await audit.record(
        db, principal.actor, "docs.page.delete", target_type="doc_page", target_id=p.id, detail={"slug": p.slug, "version": p.version}
    )
    await db.delete(p)  # children move to the top level (SET NULL)


@router.get("/pages/{slug}/versions")
async def versions(slug: str, db: DB, principal: Reader) -> dict:
    p = await _page(db, slug)
    rows = await db.scalars(select(DocVersion).where(DocVersion.page_id == p.id).order_by(DocVersion.version.desc()))
    return {
        "items": [
            {
                "version": v.version,
                "title": v.title,
                "message": v.message,
                "author": v.author,
                "created_at": v.created_at,
                "size": len(v.body),
            }
            for v in rows
        ]
    }


async def _version(db: AsyncSession, page: DocPage, n: int) -> DocVersion:
    v = await db.scalar(select(DocVersion).where(DocVersion.page_id == page.id, DocVersion.version == n))
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {n} not found")
    return v


@router.get("/pages/{slug}/versions/{n}")
async def get_version(slug: str, n: int, db: DB, principal: Reader) -> dict:
    v = await _version(db, await _page(db, slug), n)
    return {"version": v.version, "title": v.title, "body": v.body, "message": v.message, "author": v.author, "created_at": v.created_at}


@router.get("/pages/{slug}/diff")
async def diff(slug: str, db: DB, principal: Reader, from_: int = Query(alias="from", ge=1), to: int = Query(ge=1)) -> dict:
    p = await _page(db, slug)
    a, b = await _version(db, p, from_), await _version(db, p, to)
    lines = list(difflib.unified_diff(a.body.splitlines(), b.body.splitlines(), f"v{a.version}", f"v{b.version}", lineterm=""))
    added = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
    return {"from": a.version, "to": b.version, "title_changed": a.title != b.title, "diff": lines, "added": added, "removed": removed}


class Revert(BaseModel):
    version: int = Field(ge=1)
    base_version: int = Field(ge=1)


@router.post("/pages/{slug}/revert")
async def revert(slug: str, body: Revert, db: DB, principal: Editor) -> dict:
    """Restore an old version by saving it as a new one; history is never rewritten."""
    p = await _page(db, slug)
    old = await _version(db, p, body.version)
    return await save_page(
        slug,
        PageSave(
            base_version=body.base_version,
            title=old.title,
            parent_id=p.parent_id,
            body=old.body,
            tags=p.tags or [],
            message=f"Reverted to version {old.version}",
        ),
        db,
        principal,
    )


async def _backlinks(db: AsyncSession, kind: str, keys: list[str], exclude: str | None = None) -> list[dict[str, Any]]:
    wanted = {k.lower() for k in keys if k}
    out = []
    for p in await db.scalars(select(DocPage).where(DocPage.body.like(f"%[[{kind}:%"))):
        if p.id == exclude:
            continue
        if any(m.group(1) == kind and m.group(2).strip().lower() in wanted for m in REF_RE.finditer(p.body)):
            out.append(_summary(p))
    return out


@router.get("/backlinks")
async def backlinks(
    db: DB, principal: Reader, type: str = Query(pattern="^(" + "|".join(REF_TYPES) + ")$"), id: str = Query(min_length=1)
) -> dict:
    """Pages that reference an object, matched by any key the object can be written with."""
    keys = [id]
    if type == "device" and (d := await db.get(Device, id)):
        keys += [d.name, d.mac_address]
    elif type == "client" and (c := await db.get(Client, id)):
        keys += [c.name, c.alias or "", c.mac_address or "", c.ip_address or ""]
    elif type == "network" and (n := await db.get(Network, id)):
        keys += [n.name]
    elif type == "prefix" and (pf := await db.get(IpPrefix, id)):
        keys += [pf.cidr]
    elif type == "rack" and (r := await db.get(Rack, id)):
        keys += [r.name]
    elif type == "cable" and (cb := await db.get(Cable, id)):
        keys += [cb.label]
    elif type == "asset" and (a := await db.get(Asset, id)):
        keys += [a.name, a.asset_tag]
    return {"items": await _backlinks(db, type, keys)}


@router.get("/references/check")
async def check_references(db: DB, principal: Reader) -> dict:
    """Unresolved references across all pages (renamed or removed objects)."""
    broken = []
    for p in await db.scalars(select(DocPage).order_by(DocPage.title)):
        for ref in await resolve_refs(db, p.body):
            if not ref["resolved"]:
                broken.append({"page": _summary(p), "raw": ref["raw"], "type": ref["type"], "key": ref["key"]})
    return {"items": broken}

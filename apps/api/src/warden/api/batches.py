"""Bulk Operations: one change over many targets, proposed and decided once, applied item by item.

All controller writes still happen in warden.changes.engine; this router only
validates input, checks permissions and shapes responses.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.api.changes import _out as change_out
from warden.api.deps import DB, Allowed, State, require_module
from warden.changes import batches
from warden.changes.operations import ChangeRefused
from warden.models import ChangeBatch, Controller
from warden.security.rbac import P

router = APIRouter(prefix="/api/batches", tags=["bulk-ops"], dependencies=[Depends(require_module("bulk-ops"))])

Reader = Allowed(P.INVENTORY_READ)
Proposer = Allowed(P.CHANGES_PROPOSE)
Approver = Allowed(P.CHANGES_APPROVE)


async def _get(db: AsyncSession, bid: str) -> ChangeBatch:
    b = await db.get(ChangeBatch, bid)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    return b


def _summary(b: ChangeBatch, counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": b.id,
        "number": b.number,
        "ref": batches.ref(b),
        "operation": b.operation,
        "title": b.title,
        "reason": b.reason,
        "risk": b.risk,
        "protected": b.protected,
        "pause_seconds": b.pause_seconds,
        "status": b.status,
        "proposed_by": b.proposed_by,
        "proposed_at": b.created_at,
        "approved_by": b.approved_by,
        "approved_at": b.approved_at,
        "decided_note": b.decided_note,
        "started_by": b.started_by,
        "started_at": b.started_at,
        "next_at": b.next_at,
        "finished_at": b.finished_at,
        "skipped": b.skipped,
        "log": b.log,
        "counts": counts or {},
        "is_demo": b.is_demo,
    }


async def _full(db: AsyncSession, b: ChangeBatch) -> dict[str, Any]:
    rows = await batches.items(db, b)
    controllers = {c.id: c for c in await db.scalars(select(Controller))}
    counts: dict[str, int] = {}
    for cr in rows:
        counts[cr.status] = counts.get(cr.status, 0) + 1
    return {**_summary(b, counts), "items": [change_out(cr, controllers.get(cr.controller_id)) for cr in rows]}


def _refused(e: ChangeRefused) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("")
async def list_batches(
    db: DB, principal: Reader, view: Literal["open", "history", "all"] = "all", limit: int = Query(default=100, ge=1, le=500)
) -> dict:
    q = select(ChangeBatch).order_by(ChangeBatch.number.desc()).limit(limit)
    if view == "open":
        q = q.where(ChangeBatch.status.in_((*batches.BATCH_OPEN, "running")))
    elif view == "history":
        q = q.where(ChangeBatch.status.in_(batches.BATCH_FINAL))
    out = []
    for b in await db.scalars(q):
        counts: dict[str, int] = {}
        for cr in await batches.items(db, b):
            counts[cr.status] = counts.get(cr.status, 0) + 1
        out.append(_summary(b, counts))
    return {"items": out, "operations": list(batches.BULK_OPERATIONS), "max_items": batches.MAX_ITEMS}


class TargetIn(BaseModel):
    target_id: str = Field(max_length=36)
    params: dict[str, Any] = Field(default_factory=dict)


class ProposeIn(BaseModel):
    operation: str = Field(max_length=48)
    targets: list[TargetIn] = Field(max_length=batches.MAX_ITEMS)
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)
    pause_seconds: int = Field(default=0, ge=0, le=batches.MAX_PAUSE_SECONDS)


@router.post("", status_code=status.HTTP_201_CREATED)
async def propose(body: ProposeIn, db: DB, state: State, principal: Proposer) -> dict:
    try:
        b = await batches.propose(
            db,
            state,
            principal.actor,
            body.operation,
            [t.model_dump() for t in body.targets],
            body.params,
            body.reason.strip(),
            body.pause_seconds,
        )
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)


@router.get("/{bid}")
async def get_batch(bid: str, db: DB, principal: Reader) -> dict:
    return await _full(db, await _get(db, bid))


class DecideIn(BaseModel):
    note: str = Field(default="", max_length=2000)
    acknowledge_protected: bool = False


@router.post("/{bid}/approve")
async def approve(bid: str, body: DecideIn, db: DB, state: State, principal: Approver) -> dict:
    b = await _get(db, bid)
    try:
        await batches.approve(db, state, b, principal.actor, body.note.strip(), body.acknowledge_protected)
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)


@router.post("/{bid}/reject")
async def reject(bid: str, body: DecideIn, db: DB, state: State, principal: Approver) -> dict:
    b = await _get(db, bid)
    try:
        await batches.close(db, state, b, principal.actor, "rejected", body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)


@router.post("/{bid}/cancel")
async def cancel(bid: str, body: DecideIn, db: DB, state: State, principal: Proposer) -> dict:
    b = await _get(db, bid)
    from warden.changes.engine import who

    if b.proposed_by != who(principal.actor) and P.CHANGES_APPROVE not in principal.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the proposer or an approver can cancel a batch")
    try:
        await batches.close(db, state, b, principal.actor, "cancelled", body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)


@router.post("/{bid}/apply")
async def apply(bid: str, db: DB, state: State, principal: Approver) -> dict:
    b = await _get(db, bid)
    try:
        await batches.start(db, state, b, principal.actor)
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)


@router.post("/{bid}/stop")
async def stop(bid: str, body: DecideIn, db: DB, state: State, principal: Approver) -> dict:
    b = await _get(db, bid)
    try:
        await batches.stop(db, state, b, principal.actor, body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, b)

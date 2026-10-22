"""Change Approval & Recovery: propose, approve, apply, verify and revert controller changes.

Every write to a controller goes through warden.changes.engine; this router only
validates input, checks permissions and shapes responses.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.changes import engine
from warden.changes.candidates import MANY_TARGET_OPERATIONS
from warden.changes.candidates import candidates as candidate_targets
from warden.changes.operations import OPERATIONS, ChangeRefused
from warden.integrations.unifi.capabilities import evaluate
from warden.models import ChangeBatch, ChangeRequest, Controller, Setting, Site
from warden.security.rbac import P

router = APIRouter(prefix="/api/changes", tags=["changes"], dependencies=[Depends(require_module("change-engine"))])

Reader = Allowed(P.INVENTORY_READ)
Proposer = Allowed(P.CHANGES_PROPOSE)
Approver = Allowed(P.CHANGES_APPROVE)
Owner = Allowed(P.SYSTEM_MANAGE)


def _out(cr: ChangeRequest, controller: Controller | None = None) -> dict[str, Any]:
    return {
        "id": cr.id,
        "number": cr.number,
        "ref": f"CHG-{cr.number:04d}",
        "operation": cr.operation,
        "title": cr.title,
        "reason": cr.reason,
        "target": {"type": cr.target_type, "id": cr.target_id, "label": cr.target_label, "params": cr.params},
        "controller": {"id": cr.controller_id, "name": controller.name, "write_mode": controller.write_mode} if controller else None,
        "before": cr.before,
        "expected": cr.expected,
        "diff": cr.diff,
        "impact": cr.impact,
        "risk": cr.risk,
        "risk_reasons": cr.risk_reasons,
        "protected": cr.protected,
        "reversible": cr.reversible,
        "recovery": cr.recovery,
        "inverse_of_id": cr.inverse_of_id,
        "status": cr.status,
        "proposed_by": cr.proposed_by,
        "proposed_at": cr.created_at,
        "approved_by": cr.approved_by,
        "approved_at": cr.approved_at,
        "decided_note": cr.decided_note,
        "applied_by": cr.applied_by,
        "applied_at": cr.applied_at,
        "finished_at": cr.finished_at,
        "verify_deadline": cr.verify_deadline,
        "expires_at": cr.expires_at,
        "evidence": cr.evidence,
        "is_demo": cr.is_demo,
        "batch_id": cr.batch_id,
        "batch_position": cr.batch_position,
        "apply_on_approval": cr.apply_on_approval,
    }


async def _get(db: AsyncSession, cid: str) -> ChangeRequest:
    cr = await db.get(ChangeRequest, cid)
    if cr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change not found")
    return cr


def _refused(e: ChangeRefused) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(e))


async def _full(db: AsyncSession, cr: ChangeRequest) -> dict[str, Any]:
    out = _out(cr, await db.get(Controller, cr.controller_id))
    if cr.batch_id and (b := await db.get(ChangeBatch, cr.batch_id)) is not None:
        out["batch"] = {"id": b.id, "ref": f"BAT-{b.number:04d}", "status": b.status, "title": b.title}
    return out


async def _single(db: AsyncSession, cid: str) -> ChangeRequest:
    """A change decided on its own; items of a bulk operation are decided and applied with their batch."""
    cr = await _get(db, cid)
    if cr.batch_id and (b := await db.get(ChangeBatch, cr.batch_id)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"CHG-{cr.number:04d} is part of BAT-{b.number:04d}; approve, apply or cancel the batch instead"
        )
    return cr


@router.get("/operations")
async def operations(db: DB, principal: Reader) -> dict:
    """The operations, and which each controller supports at its detected version (so the UI only offers those)."""
    support: dict[str, dict[str, Any]] = {}
    for c in await db.scalars(select(Controller)):
        caps = (c.capabilities or {}).get("capabilities") or evaluate(c.application_version)["capabilities"]
        support[c.id] = {
            op.id: {
                "supported": (caps.get(op.capability) or {}).get("status") == "supported",
                "min_version": (caps.get(op.capability) or {}).get("min_version"),
            }
            for op in OPERATIONS.values()
        }
    sites = {s.id: s.controller_id for s in await db.scalars(select(Site))}
    return {
        "support": support,
        "sites": sites,
        "items": [
            {
                "id": op.id,
                "target_type": op.target_type,
                "capability": op.capability,
                "reversible": op.reversible,
                "verify_seconds": int(op.verify_window.total_seconds()),
            }
            for op in OPERATIONS.values()
        ],
    }


@router.get("/candidates")
async def candidates(db: DB, principal: Reader, operation: str) -> dict:
    """Inventory targets a many-target operation could apply to (bulk operations, automation). Checked live on proposal."""
    if operation not in MANY_TARGET_OPERATIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This operation is not offered over many targets")
    return {"operation": operation, "items": await candidate_targets(db, operation)}


@router.get("")
async def list_changes(
    db: DB,
    principal: Reader,
    view: Literal["open", "history", "all"] = "all",
    target_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    q = select(ChangeRequest).order_by(ChangeRequest.number.desc()).limit(limit)
    if view == "open":
        q = q.where(ChangeRequest.status.in_(engine.OPEN + engine.IN_FLIGHT))
    elif view == "history":
        q = q.where(ChangeRequest.status.in_(engine.FINAL))
    if target_id:
        q = q.where(ChangeRequest.target_id == target_id)
    controllers = {c.id: c for c in await db.scalars(select(Controller))}
    return {"items": [_out(cr, controllers.get(cr.controller_id)) for cr in await db.scalars(q)]}


class ProposeIn(BaseModel):
    operation: str = Field(max_length=48)
    target_id: str = Field(max_length=36)
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)


@router.post("", status_code=status.HTTP_201_CREATED)
async def propose(body: ProposeIn, db: DB, state: State, principal: Proposer) -> dict:
    try:
        cr = await engine.propose(db, state, principal.actor, body.operation, body.target_id, body.params, body.reason.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, cr)


@router.get("/{cid}")
async def get_change(cid: str, db: DB, principal: Reader) -> dict:
    return await _full(db, await _get(db, cid))


class DecideIn(BaseModel):
    note: str = Field(default="", max_length=2000)
    acknowledge_protected: bool = False
    # Only for a change proposed by an agent at "act after approval": false approves it without applying it yet.
    apply: bool | None = None


@router.post("/{cid}/approve")
async def approve(cid: str, body: DecideIn, db: DB, state: State, principal: Approver) -> dict:
    cr = await _single(db, cid)
    try:
        await engine.approve(db, state, cr, principal.actor, body.note.strip(), body.acknowledge_protected)
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ChangeRefused as e:
        raise _refused(e) from e
    if not (cr.apply_on_approval and body.apply is not False):
        return await _full(db, cr)
    # "Act after approval": the person who approved applies it now, under their own name, through the engine.
    try:
        await engine.apply(db, state, cr, principal.actor)
    except ChangeRefused as e:
        return {**await _full(db, cr), "apply_error": str(e)}
    return await _full(db, cr)


@router.post("/{cid}/reject")
async def reject(cid: str, body: DecideIn, db: DB, state: State, principal: Approver) -> dict:
    cr = await _single(db, cid)
    try:
        await engine.close(db, state, cr, principal.actor, "rejected", body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, cr)


@router.post("/{cid}/cancel")
async def cancel(cid: str, body: DecideIn, db: DB, state: State, principal: Proposer) -> dict:
    cr = await _single(db, cid)
    if cr.proposed_by != engine.who(principal.actor) and P.CHANGES_APPROVE not in principal.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the proposer or an approver can cancel a change")
    try:
        await engine.close(db, state, cr, principal.actor, "cancelled", body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, cr)


@router.post("/{cid}/apply")
async def apply(cid: str, db: DB, state: State, principal: Approver) -> dict:
    cr = await _single(db, cid)
    try:
        await engine.apply(db, state, cr, principal.actor)
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, cr)


@router.post("/{cid}/revert", status_code=status.HTTP_201_CREATED)
async def revert(cid: str, body: DecideIn, db: DB, state: State, principal: Proposer) -> dict:
    cr = await _get(db, cid)
    try:
        new = await engine.revert(db, state, cr, principal.actor, body.note.strip())
    except ChangeRefused as e:
        raise _refused(e) from e
    return await _full(db, new)


# -- installation and controller settings ------------------------------------------------


@router.get("/settings/current")
async def get_settings(db: DB, principal: Reader) -> dict:
    controllers = list(await db.scalars(select(Controller).order_by(Controller.name)))
    return {
        "require_second_person": await engine.require_second_person(db),
        "controllers": [
            {"id": c.id, "name": c.name, "write_mode": c.write_mode, "is_demo": c.is_demo, "application_version": c.application_version}
            for c in controllers
        ],
    }


class SettingsIn(BaseModel):
    require_second_person: bool


@router.put("/settings/current")
async def put_settings(body: SettingsIn, db: DB, principal: Owner) -> dict:
    row = await db.get(Setting, engine.SECOND_PERSON_KEY)
    if row is None:
        db.add(Setting(key=engine.SECOND_PERSON_KEY, value=body.require_second_person))
    else:
        row.value = body.require_second_person
    await audit.record(db, principal.actor, "change.settings", detail={"require_second_person": body.require_second_person})
    return {"require_second_person": body.require_second_person}


class WriteModeIn(BaseModel):
    write_mode: Literal["read_only", "changes"]


@router.put("/controllers/{controller_id}/write-mode")
async def set_write_mode(controller_id: str, body: WriteModeIn, db: DB, principal: Owner) -> dict:
    c = await db.get(Controller, controller_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "controller not found")
    before, c.write_mode = c.write_mode, body.write_mode
    await audit.record(
        db, principal.actor, "controller.write_mode", target_type="controller", target_id=c.id, detail={"from": before, "to": c.write_mode}
    )
    return {"id": c.id, "write_mode": c.write_mode}

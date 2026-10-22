"""The change engine: the single path by which Fabric Warden writes to a controller.

Lifecycle: propose (read live state, check capability, plan, assess risk) →
approve (someone with changes:approve; optionally a second person) → apply
(controller must allow changes; re-read and refuse on drift; send exactly one
write with a WriteAuthorization) → verify (re-read until the expected state is
seen or the window closes) → succeeded, failed, or reverted (a reversible
change whose check fails is undone straight away under the same approval).

This is the only module allowed to import the write gate's ISSUER; a test
enforces it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden import events as ev
from warden.audit import Actor
from warden.changes.operations import OPERATIONS, ChangeRefused, Operation, Target
from warden.db import utcnow
from warden.events import Event
from warden.integrations.unifi import _write_gate
from warden.integrations.unifi.capabilities import evaluate
from warden.integrations.unifi.client import UniFiError, UniFiNetworkClient, WriteAuthorization
from warden.models import ChangeRequest, Controller, Setting

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

log = logging.getLogger(__name__)

APPROVAL_WINDOW = timedelta(hours=24)
SECOND_PERSON_KEY = "changes.require_second_person"
OPEN = ("proposed", "approved")
IN_FLIGHT = ("applying", "verifying")
FINAL = ("succeeded", "failed", "reverted", "conflict", "rejected", "cancelled", "expired")


ENGINE = Actor.system("change-engine")
CLOSE_ACTION = {"rejected": "change.reject", "cancelled": "change.cancel"}


def who(actor: Actor) -> str:
    """The person behind an actor; a user's API token counts as that user."""
    return actor.name.removesuffix(" (token)")


def _note(cr: ChangeRequest, kind: str, text: str, **data: Any) -> None:
    cr.evidence = [*cr.evidence, {"at": utcnow().isoformat(), "kind": kind, "text": text, **({"data": data} if data else {})}]


async def require_second_person(db: AsyncSession) -> bool:
    row = await db.get(Setting, SECOND_PERSON_KEY)
    return bool(row and row.value)


async def _client(state: AppState, db: AsyncSession, controller: Controller) -> UniFiNetworkClient:
    key = await state.vault.reveal(db, controller.credential_id) if controller.credential_id else ""
    return state.unifi_client(controller, key)


def _capability(controller: Controller, op: Operation) -> None:
    caps = (controller.capabilities or {}).get("capabilities") or evaluate(controller.application_version)["capabilities"]
    cap = caps.get(op.capability)
    if not cap or cap.get("status") != "supported":
        raise ChangeRefused(
            f"The controller's Network application ({controller.application_version or 'unknown version'}) does not offer this "
            f"through the official API (needs {cap.get('min_version') if cap else 'a newer version'})."
        )


async def _publish(db: AsyncSession, state: AppState, kind: str, cr: ChangeRequest, severity: str = "info") -> None:
    await state.bus.publish(
        db,
        Event(
            type=kind,
            source="change-engine",
            site_id=cr.site_id,
            entity_type="change",
            entity_id=cr.id,
            severity=severity,
            payload={"number": cr.number, "title": cr.title, "status": cr.status, "operation": cr.operation, "target": cr.target_label},
        ),
    )


async def open_change_for(db: AsyncSession, target_id: str, operation: str, params: dict[str, Any]) -> int | None:
    """The number of an open change on the same target, if any.

    PoE cycles of different ports on one switch do not touch each other, so
    they may be open at the same time (a bulk PoE cycle relies on this).
    """
    rows = await db.execute(
        select(ChangeRequest.number, ChangeRequest.operation, ChangeRequest.params).where(
            ChangeRequest.target_id == target_id, ChangeRequest.status.in_(IN_FLIGHT + OPEN)
        )
    )
    for number, other_op, other_params in rows:
        if operation == other_op == "port.power_cycle" and (other_params or {}).get("port_idx") != params.get("port_idx"):
            continue
        return number
    return None


async def preview(db: AsyncSession, operation: str, target_id: str, params: dict[str, Any]) -> tuple[Target, str]:
    """What a proposal would be about, from the inventory alone: no controller read, nothing stored.

    Raises ChangeRefused for everything propose would refuse before reading the controller.
    """
    op = OPERATIONS.get(operation)
    if op is None:
        raise ChangeRefused(f"Unknown operation {operation}")
    t = await op.resolve(db, target_id, params)
    _capability(t.controller, op)
    if busy := await open_change_for(db, t.id, op.id, params):
        raise ChangeRefused(f"CHG-{busy:04d} is already open for this target")
    try:
        title = op.title(t, params)
    except (KeyError, TypeError) as e:
        raise ChangeRefused(f"The change is missing a setting: {e}") from e
    return t, title


async def propose(
    db: AsyncSession,
    state: AppState,
    actor: Actor,
    operation: str,
    target_id: str,
    params: dict[str, Any],
    reason: str,
    inverse_of: ChangeRequest | None = None,
    batch: tuple[str, int, str] | None = None,  # (batch id, position, BAT-0001)
    apply_on_approval: bool = False,  # an agent at "act after approval": the approving person's approval also applies it
) -> ChangeRequest:
    op = OPERATIONS.get(operation)
    if op is None:
        raise ChangeRefused(f"Unknown operation {operation}")
    t = await op.resolve(db, target_id, params)
    _capability(t.controller, op)
    if busy := await open_change_for(db, t.id, op.id, params):
        raise ChangeRefused(f"CHG-{busy:04d} is already open for this target; finish or cancel it first")
    try:
        async with await _client(state, db, t.controller) as api:
            before = await op.read(api, t)
    except UniFiError as e:
        raise ChangeRefused(f"Could not read the current state from the controller: {e}") from e
    if why := op.precondition(before):
        raise ChangeRefused(why)
    expected, diff = op.plan(before, params)
    a = await op.assess(db, t, before, params)
    now = utcnow()
    number = (await db.scalar(select(func.max(ChangeRequest.number))) or 0) + 1
    cr = ChangeRequest(
        number=number,
        controller_id=t.controller.id,
        site_id=t.site.id,
        operation=op.id,
        target_type=t.type,
        target_id=t.id,
        target_external_id=t.external_id,
        target_label=t.label,
        params=params,
        title=op.title(t, params),
        reason=reason,
        before=before,
        expected=expected,
        diff=diff,
        impact=a.impact,
        risk=a.risk,
        risk_reasons=a.reasons,
        protected=a.protected,
        reversible=op.reversible,
        recovery=a.recovery,
        inverse_of_id=inverse_of.id if inverse_of else None,
        status="proposed",
        proposed_by=who(actor),
        expires_at=now + APPROVAL_WINDOW,
        evidence=[],
        is_demo=t.is_demo,
        batch_id=batch[0] if batch else None,
        batch_position=batch[1] if batch else None,
        apply_on_approval=apply_on_approval and batch is None,
    )
    _note(cr, "read", "Read the current state from the controller", before=before)
    if inverse_of:
        _note(cr, "revert", f"Reverts CHG-{inverse_of.number:04d}")
    if batch:
        _note(cr, "batch", f"Item {batch[1] + 1} of {batch[2]}")
    if cr.apply_on_approval:
        _note(cr, "apply_on_approval", "Proposed by an agent at “act after approval”: approving it also applies it, as the approver")
    db.add(cr)
    await db.flush()
    await audit.record(
        db, actor, "change.propose", target_type="change", target_id=cr.id, detail={"number": number, "title": cr.title, "risk": cr.risk}
    )
    await _publish(db, state, ev.CHANGE_PROPOSED, cr)
    return cr


async def approve(db: AsyncSession, state: AppState, cr: ChangeRequest, actor: Actor, note: str, acknowledge_protected: bool) -> None:
    if cr.status != "proposed":
        raise ChangeRefused(f"Only a proposed change can be approved (this one is {cr.status})")
    if cr.expires_at < utcnow():
        raise ChangeRefused("The approval window has closed; propose the change again")
    me = who(actor)
    if cr.proposed_by == me and await require_second_person(db):
        await audit.record(
            db,
            actor,
            "change.approve.denied",
            target_type="change",
            target_id=cr.id,
            outcome="denied",
            detail={"reason": "second person required"},
        )
        await db.commit()
        raise PermissionError("This installation requires a second person to approve changes")
    if cr.protected and not acknowledge_protected:
        raise ChangeRefused("This change touches a protected path; confirm that you have read its impact")
    cr.status, cr.approved_by, cr.approved_at, cr.decided_note = "approved", me, utcnow(), note
    _note(cr, "approve", f"Approved by {me}" + (" (own proposal)" if cr.proposed_by == me else "") + (f": {note}" if note else ""))
    await audit.record(
        db, actor, "change.approve", target_type="change", target_id=cr.id, detail={"number": cr.number, "self": cr.proposed_by == me}
    )
    await _publish(db, state, ev.CHANGE_DECIDED, cr)


async def close(db: AsyncSession, state: AppState, cr: ChangeRequest, actor: Actor, status: str, note: str) -> None:
    if cr.status not in OPEN:
        raise ChangeRefused(f"This change is {cr.status}; it can no longer be {status}")
    cr.status, cr.decided_note, cr.finished_at = status, note, utcnow()
    _note(cr, status, f"{status.title()} by {who(actor)}" + (f": {note}" if note else ""))
    await audit.record(db, actor, CLOSE_ACTION[status], target_type="change", target_id=cr.id, detail={"number": cr.number})
    await _publish(db, state, ev.CHANGE_DECIDED, cr)


async def _target(db: AsyncSession, cr: ChangeRequest) -> tuple[Operation, Target]:
    op = OPERATIONS[cr.operation]
    return op, await op.resolve(db, cr.target_id, cr.params)


async def _finish(db: AsyncSession, state: AppState, cr: ChangeRequest, status: str, text: str) -> None:
    cr.status, cr.finished_at = status, utcnow()
    _note(cr, status, text)
    await audit.record(
        db,
        ENGINE,
        "change.finish",
        target_type="change",
        target_id=cr.id,
        outcome="success" if status == "succeeded" else "failure",
        detail={"number": cr.number, "status": status},
    )
    await _publish(db, state, ev.CHANGE_FINISHED, cr, "info" if status == "succeeded" else "warning")
    # Refresh the inventory so every page shows the controller's new state.
    from warden.integrations.unifi.sync import sync_controller

    controller = await db.get(Controller, cr.controller_id)
    if controller is not None:
        await sync_controller(db, state, controller, inventory=True)


async def apply(db: AsyncSession, state: AppState, cr: ChangeRequest, actor: Actor) -> None:
    if cr.status != "approved":
        raise ChangeRefused(f"Only an approved change can be applied (this one is {cr.status})")
    if cr.expires_at < utcnow():
        cr.status, cr.finished_at = "expired", utcnow()
        _note(cr, "expired", "The approval window closed before the change was applied")
        raise ChangeRefused("The approval window has closed; propose the change again")
    op, t = await _target(db, cr)
    if t.controller.write_mode != "changes":
        raise ChangeRefused("Changes are turned off for this controller. An owner can allow them on the Changes page.")
    _capability(t.controller, op)
    cr.applied_by, cr.applied_at, cr.status = who(actor), utcnow(), "applying"
    await audit.record(db, actor, "change.apply", target_type="change", target_id=cr.id, detail={"number": cr.number, "title": cr.title})
    async with await _client(state, db, t.controller) as api:
        try:
            now = await op.read(api, t)
        except UniFiError as e:
            await _finish(db, state, cr, "failed", f"Could not re-read the target before writing: {e}")
            return
        _note(cr, "read", "Re-read the target just before writing", state=now)
        if drift := op.drifted(cr.before, now):
            await _finish(
                db,
                state,
                cr,
                "conflict",
                f"The target changed since the proposal ({', '.join(drift)}); nothing was sent. Propose it again.",
            )
            return
        if why := op.precondition(now):
            await _finish(db, state, cr, "conflict", f"{why}; nothing was sent.")
            return
        auth = WriteAuthorization(cr.id, _write_gate.ISSUER)
        t.current = now
        try:
            response = await op.execute(api, t, cr.params, auth)
        except UniFiError as e:
            await _finish(db, state, cr, "failed", f"The controller refused the write: {e}")
            return
        _note(
            cr,
            "write",
            "Sent the change to the controller",
            request={"operation": cr.operation, "params": cr.params},
            response=response if isinstance(response, dict) else None,
        )
        await _publish(db, state, ev.CHANGE_APPLIED, cr)
        cr.status = "verifying"
        cr.verify_deadline = utcnow() + op.verify_window
        await _verify_once(db, state, cr, op, t, api)


async def _verify_once(db: AsyncSession, state: AppState, cr: ChangeRequest, op: Operation, t: Target, api: UniFiNetworkClient) -> None:
    progress = dict(next((e.get("data", {}).get("progress", {}) for e in reversed(cr.evidence) if e["kind"] == "verify"), {}))
    try:
        now = await op.read(api, t)
    except UniFiError as e:
        _note(cr, "verify", f"Verification read failed: {e}", progress=progress)
        now = None
    if now is not None:
        result = op.check(cr.before, cr.expected, now, progress)
        _note(cr, "verify", result.note or ("Verified" if result.ok else "Not as expected"), observed=result.observed, progress=progress)
        if result.done and result.ok:
            await _finish(db, state, cr, "succeeded", "Verified by re-reading the controller")
            return
        if result.done and not result.ok:
            await _fail_or_revert(db, state, cr, op, t, api, result.note)
            return
    if cr.verify_deadline is not None and utcnow() >= cr.verify_deadline:
        await _fail_or_revert(db, state, cr, op, t, api, "The expected state was not seen before the verification window closed")


async def _fail_or_revert(
    db: AsyncSession, state: AppState, cr: ChangeRequest, op: Operation, t: Target, api: UniFiNetworkClient, why: str
) -> None:
    inverse = op.inverse(cr.params, cr.before) if op.reversible else None
    if inverse is None:
        await _finish(db, state, cr, "failed", f"{why}. No automatic recovery exists for this operation.")
        return
    _note(cr, "revert", f"{why}. Reverting under the same approval.")
    try:
        await op.restore(api, t, cr.before, cr.params, WriteAuthorization(cr.id, _write_gate.ISSUER))
        back = await op.read(api, t)
    except UniFiError as e:
        await _finish(db, state, cr, "failed", f"The automatic revert failed too: {e}. Check the controller.")
        return
    restored = not op.drifted(cr.before, back)
    _note(cr, "verify", "Revert verified" if restored else "Revert not confirmed", observed=back)
    await _finish(
        db,
        state,
        cr,
        "reverted" if restored else "failed",
        "Reverted to the state before the change" if restored else "Could not confirm the revert. Check the controller.",
    )


async def revert(db: AsyncSession, state: AppState, cr: ChangeRequest, actor: Actor, reason: str) -> ChangeRequest:
    op = OPERATIONS[cr.operation]
    if cr.status != "succeeded":
        raise ChangeRefused("Only a change that succeeded can be reverted")
    inverse = op.inverse(cr.params, cr.before) if op.reversible else None
    if inverse is None:
        raise ChangeRefused("This operation has no inverse; it cannot be reverted")
    return await propose(db, state, actor, cr.operation, cr.target_id, inverse, reason or f"Revert CHG-{cr.number:04d}", inverse_of=cr)


async def verify_job(ctx: JobContext) -> dict[str, Any]:
    """Advance changes waiting for their result (restarts, PoE cycles) and expire stale proposals."""
    db, state = ctx.db, ctx.state
    done = 0
    for cr in list(await db.scalars(select(ChangeRequest).where(ChangeRequest.status == "verifying"))):
        op, t = await _target(db, cr)
        async with await _client(state, db, t.controller) as api:
            await _verify_once(db, state, cr, op, t, api)
        done += 1
    expired = 0
    for cr in list(await db.scalars(select(ChangeRequest).where(ChangeRequest.status.in_(OPEN), ChangeRequest.expires_at < utcnow()))):
        cr.status, cr.finished_at = "expired", utcnow()
        _note(cr, "expired", "Not applied within 24 hours of being proposed")
        expired += 1
    return {"verified": done, "expired": expired}

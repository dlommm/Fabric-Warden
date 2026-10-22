"""Bulk operations: one operation over many targets, decided once, applied item by item.

A batch adds no write path. Each item is an ordinary change request proposed
through `engine.propose` (live read, plan, risk, protected path), approved
through `engine.approve` and applied through `engine.apply` (fresh re-read,
drift check, one write with an engine-minted authorization, verification).
The batch only orders the items, waits for each to finish, pauses between
them when asked, and stops at the first item that does not succeed: the
items after it are cancelled and never sent.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.audit import Actor
from warden.changes import engine
from warden.changes.candidates import MANY_TARGET_OPERATIONS
from warden.changes.operations import ChangeRefused
from warden.db import utcnow
from warden.events import Event
from warden.models import ChangeBatch, ChangeRequest, Controller

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

# Renames and zone moves are one-off edits and stay single changes.
BULK_OPERATIONS = MANY_TARGET_OPERATIONS
MAX_ITEMS = 50
MAX_PAUSE_SECONDS = 3600
RISK_RANK = {"low": 0, "medium": 1, "high": 2}
BATCH_OPEN = ("proposed", "approved")
BATCH_FINAL = ("succeeded", "stopped", "rejected", "cancelled", "expired")
RUNNER = Actor.system("bulk-ops")

BATCH_PROPOSED = "change.batch.proposed"
BATCH_DECIDED = "change.batch.decided"
BATCH_FINISHED = "change.batch.finished"


def ref(b: ChangeBatch) -> str:
    return f"BAT-{b.number:04d}"


def batch_title(operation: str, params: dict[str, Any], made: list[ChangeRequest]) -> str:
    on = params.get("enabled", params.get("value"))
    verb = {
        "port.power_cycle": "Power-cycle PoE on",
        "device.restart": "Restart",
        "firewall_policy.set_logging": f"Turn {'on' if on else 'off'} logging for",
        "firewall_policy.set_enabled": f"{'Enable' if on else 'Disable'}",
        "network.set_internet_access": f"{'Allow' if on else 'Block'} internet access for",
        "network.set_isolation": f"Turn {'on' if on else 'off'} isolation for",
    }[operation]
    noun = {"port.power_cycle": "port", "device.restart": "device"}.get(operation) or (
        "network" if operation.startswith("network.") else "policy"
    )
    n = len(made)
    plural = noun if n == 1 else ("policies" if noun == "policy" else noun + "s")
    names = ", ".join(cr.target_label for cr in made[:3]) + ("…" if n > 3 else "")
    return f"{verb} {n} {plural}: {names}"


def _log(b: ChangeBatch, kind: str, text: str, **data: Any) -> None:
    b.log = [*b.log, {"at": utcnow().isoformat(), "kind": kind, "text": text, **({"data": data} if data else {})}]


async def items(db: AsyncSession, b: ChangeBatch) -> list[ChangeRequest]:
    return list(await db.scalars(select(ChangeRequest).where(ChangeRequest.batch_id == b.id).order_by(ChangeRequest.batch_position)))


async def _publish(db: AsyncSession, state: AppState, kind: str, b: ChangeBatch, severity: str = "info") -> None:
    await state.bus.publish(
        db,
        Event(
            type=kind,
            source="bulk-ops",
            entity_type="change_batch",
            entity_id=b.id,
            severity=severity,
            payload={"number": b.number, "title": b.title, "status": b.status, "operation": b.operation},
        ),
    )


async def propose(
    db: AsyncSession,
    state: AppState,
    actor: Actor,
    operation: str,
    targets: list[dict[str, Any]],
    params: dict[str, Any],
    reason: str,
    pause_seconds: int,
) -> ChangeBatch:
    """Propose one change per target. Targets the engine refuses are listed as skipped; at least one must remain."""
    if operation not in BULK_OPERATIONS:
        raise ChangeRefused("This operation is not offered in bulk; propose it as a single change")
    if not targets:
        raise ChangeRefused("Choose at least one target")
    if len(targets) > MAX_ITEMS:
        raise ChangeRefused(f"A bulk operation covers at most {MAX_ITEMS} targets")
    if not 0 <= pause_seconds <= MAX_PAUSE_SECONDS:
        raise ChangeRefused(f"The pause between items must be between 0 and {MAX_PAUSE_SECONDS} seconds")
    seen: set[tuple[str, Any]] = set()
    unique = []
    for t in targets:
        key = (str(t.get("target_id")), (t.get("params") or {}).get("port_idx"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    number = (await db.scalar(select(func.max(ChangeBatch.number))) or 0) + 1
    b = ChangeBatch(
        number=number,
        operation=operation,
        title="",
        reason=reason,
        risk="low",
        pause_seconds=pause_seconds,
        status="proposed",
        proposed_by=engine.who(actor),
        skipped=[],
        log=[],
    )
    db.add(b)
    await db.flush()
    made: list[ChangeRequest] = []
    skipped: list[dict[str, Any]] = []
    for t in unique:
        item_params = {**params, **(t.get("params") or {})}
        try:
            cr = await engine.propose(
                db, state, actor, operation, str(t.get("target_id")), item_params, reason, batch=(b.id, len(made), ref(b))
            )
        except ChangeRefused as e:
            skipped.append({"target_id": t.get("target_id"), "params": t.get("params") or {}, "reason": str(e)})
            continue
        made.append(cr)
    if not made:
        raise ChangeRefused("None of the targets can take this change: " + "; ".join(s["reason"] for s in skipped[:5]))
    b.skipped = skipped
    b.risk = max((cr.risk for cr in made), key=lambda r: RISK_RANK.get(r, 2))
    b.protected = any(cr.protected for cr in made)
    b.is_demo = all(cr.is_demo for cr in made)
    b.title = batch_title(operation, params, made)
    _log(
        b,
        "propose",
        f"Proposed {len(made)} item{'s' if len(made) > 1 else ''}" + (f"; {len(skipped)} target(s) skipped" if skipped else ""),
    )
    await audit.record(
        db,
        actor,
        "change.batch.propose",
        target_type="change_batch",
        target_id=b.id,
        detail={"number": number, "operation": operation, "items": [cr.number for cr in made], "skipped": len(skipped), "risk": b.risk},
    )
    await _publish(db, state, BATCH_PROPOSED, b)
    return b


async def approve(db: AsyncSession, state: AppState, b: ChangeBatch, actor: Actor, note: str, acknowledge_protected: bool) -> None:
    """One decision for the whole batch; every item is approved through the engine with the same note."""
    if b.status != "proposed":
        raise ChangeRefused(f"Only a proposed batch can be approved (this one is {b.status})")
    if b.protected and not acknowledge_protected:
        raise ChangeRefused("At least one item touches a protected path; confirm that you have read the impact")
    rows = [cr for cr in await items(db, b) if cr.status == "proposed"]
    if not rows:
        raise ChangeRefused("No item of this batch is still waiting for approval")
    for cr in rows:
        await engine.approve(db, state, cr, actor, note or f"Approved with {ref(b)}", acknowledge_protected)
    me = engine.who(actor)
    b.status, b.approved_by, b.approved_at, b.decided_note = "approved", me, utcnow(), note
    _log(b, "approve", f"Approved by {me}" + (" (own proposal)" if b.proposed_by == me else "") + (f": {note}" if note else ""))
    await audit.record(
        db, actor, "change.batch.approve", target_type="change_batch", target_id=b.id, detail={"number": b.number, "items": len(rows)}
    )
    await _publish(db, state, BATCH_DECIDED, b)


async def close(db: AsyncSession, state: AppState, b: ChangeBatch, actor: Actor, status: str, note: str) -> None:
    """Reject or cancel a batch that has not started; every open item is closed the same way."""
    if b.status not in BATCH_OPEN:
        raise ChangeRefused(f"This batch is {b.status}; it can no longer be {status}")
    for cr in await items(db, b):
        if cr.status in engine.OPEN:
            await engine.close(db, state, cr, actor, status, note or f"{status.title()} with {ref(b)}")
    b.status, b.decided_note, b.finished_at = status, note, utcnow()
    _log(b, status, f"{status.title()} by {engine.who(actor)}" + (f": {note}" if note else ""))
    await audit.record(
        db, actor, f"change.batch.{'reject' if status == 'rejected' else 'cancel'}", target_type="change_batch", target_id=b.id
    )
    await _publish(db, state, BATCH_DECIDED, b)


async def start(db: AsyncSession, state: AppState, b: ChangeBatch, actor: Actor) -> None:
    if b.status != "approved":
        raise ChangeRefused(f"Only an approved batch can be applied (this one is {b.status})")
    for cr in await items(db, b):
        controller = await db.get(Controller, cr.controller_id)
        if cr.status == "approved" and (controller is None or controller.write_mode != "changes"):
            raise ChangeRefused("Changes are turned off for this controller. An owner can allow them on the Changes page.")
    b.status, b.started_by, b.started_at = "running", engine.who(actor), utcnow()
    _log(b, "start", f"Started by {b.started_by}" + (f"; {b.pause_seconds} s between items" if b.pause_seconds else ""))
    await audit.record(db, actor, "change.batch.apply", target_type="change_batch", target_id=b.id, detail={"number": b.number})
    await advance(db, state, b, actor)


async def stop(db: AsyncSession, state: AppState, b: ChangeBatch, actor: Actor, note: str) -> None:
    """Stop a running batch before its next item; the item in progress finishes and is verified as usual."""
    if b.status != "running":
        raise ChangeRefused(f"Only a running batch can be stopped (this one is {b.status})")
    await _halt(db, state, b, f"Stopped by {engine.who(actor)}" + (f": {note}" if note else ""), actor)


async def _halt(db: AsyncSession, state: AppState, b: ChangeBatch, why: str, actor: Actor) -> None:
    left = 0
    for cr in await items(db, b):
        if cr.status in engine.OPEN:
            await engine.close(db, state, cr, actor, "cancelled", f"Not applied: {ref(b)} stopped. {why}")
            left += 1
    b.status, b.finished_at, b.next_at = "stopped", utcnow(), None
    _log(b, "stopped", why + (f" {left} item{'s were' if left != 1 else ' was'} not applied." if left else ""))
    await audit.record(
        db,
        actor,
        "change.batch.finish",
        target_type="change_batch",
        target_id=b.id,
        outcome="failure",
        detail={"number": b.number, "status": "stopped"},
    )
    await _publish(db, state, BATCH_FINISHED, b, "warning")


def _actor_for(b: ChangeBatch, actor: Actor | None) -> Actor:
    # Items applied later by the scheduler are applied on behalf of the person who started the batch.
    return actor or Actor("system", None, b.started_by or RUNNER.name)


async def advance(db: AsyncSession, state: AppState, b: ChangeBatch, actor: Actor | None = None) -> None:
    """Move a running batch forward as far as it can go now: apply the next item, or wait, or stop, or finish."""
    if b.status != "running":
        return
    who = _actor_for(b, actor)
    rows = await items(db, b)
    for i, cr in enumerate(rows):
        if cr.status == "succeeded":
            continue
        if cr.status in engine.IN_FLIGHT:
            return  # verifying: the change engine's verify job advances it; we pick up when it finishes
        if cr.status != "approved":
            await _halt(
                db, state, b, f"CHG-{cr.number:04d} ended {cr.status}; the batch stops at the first item that does not succeed.", who
            )
            return
        prev = rows[i - 1] if i else None
        if prev is not None and b.pause_seconds and prev.finished_at is not None:
            ready = prev.finished_at + timedelta(seconds=b.pause_seconds)
            if utcnow() < ready:
                b.next_at = ready
                return
        b.next_at = None
        try:
            await engine.apply(db, state, cr, who)
        except ChangeRefused as e:
            await _halt(db, state, b, f"CHG-{cr.number:04d} could not be applied: {e}", who)
            return
        _log(b, "item", f"CHG-{cr.number:04d} {cr.target_label}: {cr.status}", change_id=cr.id, status=cr.status)
        if cr.status in engine.IN_FLIGHT:
            return
        if cr.status != "succeeded":
            await _halt(
                db, state, b, f"CHG-{cr.number:04d} ended {cr.status}; the batch stops at the first item that does not succeed.", who
            )
            return
        if b.pause_seconds and i + 1 < len(rows):
            b.next_at = utcnow() + timedelta(seconds=b.pause_seconds)
            return
    b.status, b.finished_at, b.next_at = "succeeded", utcnow(), None
    _log(b, "succeeded", f"All {len(rows)} item{'s' if len(rows) != 1 else ''} succeeded")
    await audit.record(
        db, RUNNER, "change.batch.finish", target_type="change_batch", target_id=b.id, detail={"number": b.number, "status": "succeeded"}
    )
    await _publish(db, state, BATCH_FINISHED, b)


async def advance_job(ctx: JobContext) -> dict[str, Any]:
    """Continue running batches: after a verification finished, or once the pause between items has passed."""
    moved = 0
    for b in list(await ctx.db.scalars(select(ChangeBatch).where(ChangeBatch.status == "running"))):
        before = (b.status, len(b.log))
        await advance(ctx.db, ctx.state, b)
        moved += (b.status, len(b.log)) != before
    expired = 0
    for b in list(await ctx.db.scalars(select(ChangeBatch).where(ChangeBatch.status.in_(BATCH_OPEN)))):
        rows = await items(ctx.db, b)
        live = [cr for cr in rows if cr.status not in ("cancelled", "rejected")]
        if live and all(cr.status == "expired" for cr in live):
            b.status, b.finished_at = "expired", utcnow()
            _log(b, "expired", "Not applied within 24 hours of being proposed")
            expired += 1
    return {"advanced": moved, "expired": expired}

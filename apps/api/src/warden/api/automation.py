"""Automation Studio: rules that propose changes or send notifications. A rule never applies a change."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit, automation
from warden.api.deps import DB, Allowed, require_module
from warden.changes.candidates import MANY_TARGET_OPERATIONS
from warden.changes.engine import who
from warden.changes.operations import OPERATIONS
from warden.models import AutomationRule, AutomationRun, ChangeRequest
from warden.security.rbac import P

router = APIRouter(prefix="/api/automation", tags=["automation"], dependencies=[Depends(require_module("automation"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.CHANGES_PROPOSE)


def _run_out(r: AutomationRun, numbers: dict[str, int] | None = None) -> dict[str, Any]:
    n = (numbers or {}).get(r.change_id or "")
    return {
        "id": r.id,
        "rule_id": r.rule_id,
        "at": r.at,
        "trigger": r.trigger,
        "event_id": r.event_id,
        "event_type": r.event_type,
        "outcome": r.outcome,
        "detail": r.detail,
        "change_id": r.change_id,
        "change_ref": f"CHG-{n:04d}" if n else None,
    }


def _out(rule: AutomationRule, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.enabled,
        "trigger_kind": rule.trigger_kind,
        "event_type": rule.event_type,
        "interval_minutes": rule.interval_minutes,
        "conditions": rule.conditions,
        "action": rule.action,
        "cooldown_minutes": rule.cooldown_minutes,
        "created_by": rule.created_by,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "last_run_at": rule.last_run_at,
        "stats": stats or {},
    }


async def _get(db: AsyncSession, rid: str) -> AutomationRule:
    rule = await db.get(AutomationRule, rid)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    return rule


async def _numbers(db: AsyncSession, runs: list[AutomationRun]) -> dict[str, int]:
    ids = [r.change_id for r in runs if r.change_id]
    if not ids:
        return {}
    return dict((await db.execute(select(ChangeRequest.id, ChangeRequest.number).where(ChangeRequest.id.in_(ids)))).all())


@router.get("/meta")
async def meta(principal: Reader) -> dict:
    """What a rule can be built from: trigger events, condition operators, and operations it may propose."""
    return {
        "events": [{"type": k, "label": v, "notify_only": k in automation.NOTIFY_ONLY} for k, v in automation.TRIGGER_EVENTS.items()],
        "ops": list(automation.OPS),
        "fields": ["severity", "entity_type", "entity_id", "site_id", "source", "payload.<key>"],
        "operations": [{"id": op.id, "target_type": op.target_type} for op in OPERATIONS.values() if op.id in MANY_TARGET_OPERATIONS],
        "target_entity": automation.TARGET_ENTITY,
        "min_interval_minutes": automation.MIN_INTERVAL_MINUTES,
    }


@router.get("/rules")
async def list_rules(db: DB, principal: Reader) -> dict:
    rows = (
        await db.execute(
            select(AutomationRun.rule_id, AutomationRun.outcome, func.count()).group_by(AutomationRun.rule_id, AutomationRun.outcome)
        )
    ).all()
    stats: dict[str, dict[str, int]] = {}
    for rid, outcome, n in rows:
        stats.setdefault(rid, {})[outcome] = n
    rules = await db.scalars(select(AutomationRule).order_by(AutomationRule.name))
    return {"items": [_out(r, stats.get(r.id, {})) for r in rules]}


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    trigger_kind: Literal["event", "schedule"]
    event_type: str | None = Field(default=None, max_length=64)
    interval_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 31)
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    action: dict[str, Any]
    cooldown_minutes: int = Field(default=60, ge=0, le=60 * 24 * 7)


def _validated(body: RuleIn) -> dict[str, Any]:
    data = body.model_dump()
    data["name"] = data["name"].strip()
    if data["trigger_kind"] == "event":
        data["interval_minutes"] = None
    else:
        data["event_type"] = None
    try:
        automation.validate(data)
    except automation.RuleInvalid as e:
        raise HTTPException(422, str(e)) from e
    return data


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, db: DB, principal: Editor) -> dict:
    data = _validated(body)
    rule = AutomationRule(**data, enabled=False, created_by=who(principal.actor), cursor_seq=await automation.latest_seq(db))
    db.add(rule)
    await db.flush()
    await audit.record(
        db, principal.actor, "automation.rule.create", target_type="automation_rule", target_id=rule.id, detail={"name": rule.name}
    )
    return _out(rule)


@router.get("/rules/{rid}")
async def get_rule(rid: str, db: DB, principal: Reader) -> dict:
    rule = await _get(db, rid)
    runs = list(await db.scalars(select(AutomationRun).where(AutomationRun.rule_id == rid).order_by(AutomationRun.at.desc()).limit(100)))
    numbers = await _numbers(db, runs)
    return {**_out(rule), "runs": [_run_out(r, numbers) for r in runs]}


@router.put("/rules/{rid}")
async def update_rule(rid: str, body: RuleIn, db: DB, principal: Editor) -> dict:
    rule = await _get(db, rid)
    data = _validated(body)
    for k, v in data.items():
        setattr(rule, k, v)
    await audit.record(
        db, principal.actor, "automation.rule.update", target_type="automation_rule", target_id=rule.id, detail={"name": rule.name}
    )
    return _out(rule)


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/rules/{rid}/enabled")
async def set_enabled(rid: str, body: EnabledIn, db: DB, principal: Editor) -> dict:
    rule = await _get(db, rid)
    if body.enabled and not rule.enabled:
        # Start from now: enabling a rule never replays old events.
        rule.cursor_seq = await automation.latest_seq(db)
        rule.last_run_at = None if rule.trigger_kind == "event" else rule.last_run_at
    rule.enabled = body.enabled
    await audit.record(
        db,
        principal.actor,
        "automation.rule.enable" if body.enabled else "automation.rule.disable",
        target_type="automation_rule",
        target_id=rule.id,
        detail={"name": rule.name},
    )
    return _out(rule)


@router.delete("/rules/{rid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rid: str, db: DB, principal: Editor) -> None:
    rule = await _get(db, rid)
    await audit.record(
        db, principal.actor, "automation.rule.delete", target_type="automation_rule", target_id=rule.id, detail={"name": rule.name}
    )
    await db.delete(rule)


@router.post("/rules/{rid}/dry-run")
async def dry_run_saved(rid: str, db: DB, principal: Reader, days: int = Query(default=7, ge=1, le=30)) -> dict:
    return await automation.dry_run(db, await _get(db, rid), days)


@router.post("/dry-run")
async def dry_run_draft(body: RuleIn, db: DB, principal: Reader, days: int = Query(default=7, ge=1, le=30)) -> dict:
    """Try a rule before saving it. Nothing is stored, proposed or sent."""
    data = _validated(body)
    draft = AutomationRule(**data, id="draft", enabled=False, created_by=who(principal.actor), cursor_seq=0)
    return await automation.dry_run(db, draft, days)


@router.get("/runs")
async def list_runs(db: DB, principal: Reader, rule_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> dict:
    q = select(AutomationRun).order_by(AutomationRun.at.desc()).limit(limit)
    if rule_id:
        q = q.where(AutomationRun.rule_id == rule_id)
    runs = list(await db.scalars(q))
    numbers = await _numbers(db, runs)
    return {"items": [_run_out(r, numbers) for r in runs]}

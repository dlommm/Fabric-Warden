"""Monitoring: alerts, alert rules and maintenance windows."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from warden import audit, metrics, monitoring
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.models import Alert, AlertRule, MaintenanceWindow, NotificationChannel
from warden.security.rbac import P

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"], dependencies=[Depends(require_module("monitoring"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.MONITORING_MANAGE)
Severity = Literal["critical", "warning", "info"]


def _alert(a: Alert, rule: AlertRule | None) -> dict[str, Any]:
    unit = metrics.METRICS[rule.metric].unit if rule and rule.metric in metrics.METRICS else ""
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "rule": rule.name if rule else None,
        "metric": rule.metric if rule else None,
        "unit": unit,
        "comparator": rule.comparator if rule else None,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "series_id": a.series_id,
        "label": a.label,
        "state": a.state,
        "severity": a.severity,
        "value": a.value,
        "threshold": a.threshold,
        "summary": monitoring.summary(rule, a) if rule else a.label,
        "pending_since": a.pending_since,
        "fired_at": a.fired_at,
        "resolved_at": a.resolved_at,
        "resolution": a.resolution,
        "acknowledged_at": a.acknowledged_at,
        "acknowledged_by": a.acknowledged_by,
        "silenced": a.silenced,
        "is_demo": a.is_demo,
    }


SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@router.get("/alerts")
async def list_alerts(
    db: DB, principal: Reader, state: Literal["active", "resolved", "all"] = "active", entity_id: str | None = None, limit: int = 200
) -> dict:
    rules = {r.id: r for r in await db.scalars(select(AlertRule))}
    q = select(Alert)
    if state == "active":
        q = q.where(Alert.state.in_(["pending", "firing"]))
    elif state == "resolved":
        q = q.where(Alert.state == "resolved")
    if entity_id:
        q = q.where(Alert.entity_id == entity_id)
    q = q.order_by(func.coalesce(Alert.resolved_at, Alert.fired_at, Alert.pending_since).desc()).limit(min(max(limit, 1), 500))
    rows = list(await db.scalars(q))
    if state == "active":
        rows.sort(key=lambda a: (a.state != "firing", SEVERITY_ORDER.get(a.severity, 3), a.acknowledged_at is not None, a.label))
    counts = dict(
        (await db.execute(select(Alert.state, func.count()).where(Alert.state.in_(["pending", "firing"])).group_by(Alert.state))).all()  # type: ignore[arg-type]
    )
    firing = list(await db.scalars(select(Alert).where(Alert.state == "firing")))
    return {
        "items": [_alert(a, rules.get(a.rule_id)) for a in rows],
        "counts": {
            "firing": counts.get("firing", 0),
            "pending": counts.get("pending", 0),
            "critical": sum(a.severity == "critical" and not a.acknowledged_at for a in firing),
            "unacknowledged": sum(not a.acknowledged_at for a in firing),
            "silenced": sum(a.silenced for a in firing),
        },
    }


@router.post("/alerts/{alert_id}/ack")
async def acknowledge(alert_id: str, db: DB, principal: Manager) -> dict:
    a = await db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    if a.state != "firing":
        raise HTTPException(status.HTTP_409_CONFLICT, "only firing alerts can be acknowledged")
    a.acknowledged_at, a.acknowledged_by = utcnow(), principal.user.username
    await audit.record(db, principal.actor, "monitoring.alert.ack", target_type="alert", target_id=a.id, detail={"label": a.label})
    return _alert(a, await db.get(AlertRule, a.rule_id))


@router.post("/evaluate")
async def evaluate_now(state: State, db: DB, principal: Manager) -> dict:
    return await monitoring.evaluate(state, db)


# -- rules ---------------------------------------------------------------------------


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field("", max_length=2000)
    metric: str
    entity_ids: list[str] = Field(default_factory=list, max_length=100)
    comparator: Literal["gt", "lt"]
    threshold: float
    for_seconds: int = Field(0, ge=0, le=86400)
    severity: Severity = "warning"
    enabled: bool = True
    channel_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("metric")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in metrics.METRICS:
            raise ValueError(f"unknown metric {v}")
        return v


class RulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    entity_ids: list[str] | None = Field(None, max_length=100)
    comparator: Literal["gt", "lt"] | None = None
    threshold: float | None = None
    for_seconds: int | None = Field(None, ge=0, le=86400)
    severity: Severity | None = None
    enabled: bool | None = None
    channel_ids: list[str] | None = Field(None, max_length=20)


def _rule(r: AlertRule, active: int = 0) -> dict[str, Any]:
    m = metrics.METRICS.get(r.metric)
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "metric": r.metric,
        "metric_label": m.label if m else r.metric,
        "unit": m.unit if m else "",
        "entity_type": m.entity_type if m else None,
        "entity_ids": r.entity_ids,
        "comparator": r.comparator,
        "threshold": r.threshold,
        "for_seconds": r.for_seconds,
        "severity": r.severity,
        "enabled": r.enabled,
        "channel_ids": r.channel_ids,
        "builtin": r.builtin_key is not None,
        "active_alerts": active,
        "updated_at": r.updated_at,
    }


async def _check_channels(db: Any, ids: list[str]) -> None:
    if not ids:
        return
    found = set(await db.scalars(select(NotificationChannel.id).where(NotificationChannel.id.in_(ids))))
    if missing := [i for i in ids if i not in found]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"channel_ids: unknown channel {missing[0]}")


@router.get("/rules")
async def list_rules(db: DB, principal: Reader) -> dict:
    await monitoring.ensure_builtin_rules(db)
    active = dict(
        (await db.execute(select(Alert.rule_id, func.count()).where(Alert.state.in_(["pending", "firing"])).group_by(Alert.rule_id))).all()  # type: ignore[arg-type]
    )
    rows = await db.scalars(select(AlertRule).order_by(AlertRule.name))
    return {"items": [_rule(r, active.get(r.id, 0)) for r in rows]}


async def _get_rule(db: Any, rule_id: str) -> AlertRule:
    r = await db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return r


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, db: DB, principal: Manager) -> dict:
    await _check_channels(db, body.channel_ids)
    r = AlertRule(**body.model_dump())
    db.add(r)
    await db.flush()
    await audit.record(db, principal.actor, "monitoring.rule.create", target_type="alert_rule", target_id=r.id, detail=body.model_dump())
    return _rule(r)


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: str, body: RulePatch, db: DB, principal: Manager) -> dict:
    r = await _get_rule(db, rule_id)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "channel_ids" in changes:
        await _check_channels(db, changes["channel_ids"])
    for k, v in changes.items():
        setattr(r, k, v)
    await db.flush()
    await audit.record(db, principal.actor, "monitoring.rule.update", target_type="alert_rule", target_id=r.id, detail=changes)
    return _rule(r)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: str, db: DB, principal: Manager) -> None:
    r = await _get_rule(db, rule_id)
    await db.delete(r)
    await audit.record(db, principal.actor, "monitoring.rule.delete", target_type="alert_rule", target_id=rule_id, detail={"name": r.name})


# -- maintenance windows --------------------------------------------------------------


class WindowIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    starts_at: datetime | None = None  # default: now
    ends_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=5, le=60 * 24 * 14)
    entity_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def span(self) -> WindowIn:
        if self.ends_at is None and self.duration_minutes is None:
            raise ValueError("give an end time or a duration")
        for f in ("starts_at", "ends_at"):
            v = getattr(self, f)
            if v is not None and v.tzinfo is None:
                raise ValueError(f"{f} needs a timezone")
        return self


def _window(w: MaintenanceWindow, now: datetime) -> dict[str, Any]:
    st = "active" if w.starts_at <= now < w.ends_at else "scheduled" if w.starts_at > now else "ended"
    return {
        "id": w.id,
        "name": w.name,
        "starts_at": w.starts_at,
        "ends_at": w.ends_at,
        "entity_ids": w.entity_ids,
        "reason": w.reason,
        "created_by": w.created_by,
        "state": st,
    }


@router.get("/maintenance")
async def list_windows(db: DB, principal: Reader) -> dict:
    now = utcnow()
    rows = await db.scalars(
        select(MaintenanceWindow).where(MaintenanceWindow.ends_at > now - timedelta(days=30)).order_by(MaintenanceWindow.starts_at.desc())
    )
    return {"items": [_window(w, now) for w in rows]}


@router.post("/maintenance", status_code=status.HTTP_201_CREATED)
async def create_window(body: WindowIn, db: DB, principal: Manager) -> dict:
    now = utcnow()
    start = body.starts_at or now
    end = body.ends_at or start + timedelta(minutes=body.duration_minutes or 60)
    if end <= start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ends_at: must be after the start")
    if end <= now:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ends_at: must be in the future")
    w = MaintenanceWindow(
        name=body.name, starts_at=start, ends_at=end, entity_ids=body.entity_ids, reason=body.reason, created_by=principal.user.username
    )
    db.add(w)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "monitoring.maintenance.create",
        target_type="maintenance_window",
        target_id=w.id,
        detail={"name": w.name, "starts_at": start.isoformat(), "ends_at": end.isoformat(), "entities": len(w.entity_ids)},
    )
    return _window(w, now)


@router.post("/maintenance/{window_id}/end")
async def end_window(window_id: str, db: DB, principal: Manager) -> dict:
    w = await db.get(MaintenanceWindow, window_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "maintenance window not found")
    now = utcnow()
    if w.ends_at > now:
        w.ends_at = max(now, w.starts_at)
        await audit.record(db, principal.actor, "monitoring.maintenance.end", target_type="maintenance_window", target_id=w.id)
    return _window(w, now)

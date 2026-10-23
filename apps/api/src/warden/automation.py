"""Automation Studio: rules that react to events or a schedule.

A rule is a trigger (an event type from the event log, or a fixed interval),
conditions on the event, and one action. The only actions are:

* **propose** a change through the change engine. The proposal waits for a
  person to approve and apply it like any other; a rule never approves,
  applies or writes.
* **notify** through the notifications module's channels.

The `automation.evaluate` job reads new events from the persisted event log
(by sequence number, so nothing is missed between runs or across restarts)
and records every firing as a run. A dry run replays recent matching events
and says what the rule would have done, touching nothing.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit, notify
from warden import events as ev
from warden.audit import Actor
from warden.changes import engine
from warden.changes.candidates import MANY_TARGET_OPERATIONS
from warden.changes.operations import OPERATIONS, ChangeRefused
from warden.db import utcnow
from warden.models import AutomationRule, AutomationRun, EventRecord, NotificationChannel

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

# Events a rule may react to. Change events are left out so a rule cannot feed on its own proposals.
TRIGGER_EVENTS: dict[str, str] = {
    ev.ALERT_FIRING: "An alert started firing",
    ev.ALERT_RESOLVED: "An alert resolved",
    ev.DEVICE_STATE_CHANGED: "A device changed state",
    ev.DEVICE_MISSING: "A device is missing from the controller",
    ev.DEVICE_DISCOVERED: "A new device was discovered",
    ev.DEVICE_FIRMWARE_CHANGED: "A device's firmware changed",
    ev.CLIENT_CONNECTED: "A client connected",
    ev.CLIENT_DISCONNECTED: "A client disconnected",
    ev.CLIENT_MOVED: "A client changed address or attachment",
    ev.CONTROLLER_STATUS_CHANGED: "A controller's connection status changed",
    ev.CONFIG_CHANGED: "The controller configuration changed",
    ev.CABLING_FINDING_OPENED: "Documented cabling disagrees with the controller",
    ev.CABLING_FINDING_RESOLVED: "A cabling disagreement cleared",
    ev.CHANGE_FINISHED: "A change finished (notify only)",
}
NOTIFY_ONLY = {ev.CHANGE_FINISHED}
OPS = ("eq", "ne", "in", "not_in", "contains", "gte", "lte", "exists")
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# Which event entity an operation's target is: a PoE cycle targets a device (plus the port number).
TARGET_ENTITY = {"device": "device", "port": "device", "firewall_policy": "firewall_policy", "network": "network", "zone": "zone"}
MIN_INTERVAL_MINUTES = 15
DRY_RUN_EVENTS = 50
BATCH_EVENTS = 200


class RuleInvalid(ValueError):
    """The rule definition is not usable; the message says why."""


def rule_actor(rule: AutomationRule) -> Actor:
    return Actor("system", rule.id, f"Automation: {rule.name}"[:64])


# -- validation --------------------------------------------------------------------------


def validate(data: dict[str, Any]) -> None:
    kind = data.get("trigger_kind")
    action = data.get("action") or {}
    if kind == "event":
        if data.get("event_type") not in TRIGGER_EVENTS:
            raise RuleInvalid("Choose an event type the rule reacts to")
    elif kind == "schedule":
        minutes = data.get("interval_minutes")
        if not isinstance(minutes, int) or minutes < MIN_INTERVAL_MINUTES:
            raise RuleInvalid(f"A scheduled rule runs at most every {MIN_INTERVAL_MINUTES} minutes")
    else:
        raise RuleInvalid("A rule is triggered by an event or by a schedule")
    for c in data.get("conditions") or []:
        field = str(c.get("field") or "")
        if c.get("op") not in OPS:
            raise RuleInvalid(f"Unknown condition operator {c.get('op')!r}")
        if field not in ("severity", "entity_type", "entity_id", "site_id", "source") and not field.startswith("payload."):
            raise RuleInvalid(f"Conditions can test severity, entity_type, entity_id, site_id, source or payload.<key>, not {field!r}")
    if kind == "schedule" and data.get("conditions"):
        raise RuleInvalid("A scheduled rule has no event to test; remove its conditions")
    if action.get("kind") == "propose":
        if action.get("operation") not in MANY_TARGET_OPERATIONS:
            raise RuleInvalid("Choose an operation to propose: a restart, a PoE cycle, or switching a policy or network setting")
        if kind == "event" and data.get("event_type") in NOTIFY_ONLY:
            raise RuleInvalid("A rule triggered by a finished change can only notify")
        target = action.get("target")
        if target not in ("event", "fixed") or (kind == "schedule" and target != "fixed"):
            raise RuleInvalid("Say which target to propose the change for: the event's device or object, or a fixed one")
        if target == "fixed" and not action.get("target_id"):
            raise RuleInvalid("Choose the fixed target")
        if not isinstance(action.get("params") or {}, dict):
            raise RuleInvalid("Operation settings must be an object")
    elif action.get("kind") == "notify":
        if not isinstance(action.get("channel_ids") or [], list):
            raise RuleInvalid("Channels must be a list")
        if action.get("severity", "info") not in SEVERITY_RANK:
            raise RuleInvalid("Severity is info, warning or critical")
    else:
        raise RuleInvalid("A rule's action is to propose a change or to send a notification")


# -- matching ----------------------------------------------------------------------------


def _value(e: EventRecord, field: str) -> Any:
    if field.startswith("payload."):
        cur: Any = e.payload or {}
        for part in field.removeprefix("payload.").split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur
    return getattr(e, field, None)


def _holds(c: dict[str, Any], actual: Any) -> bool:
    op, want, field = c.get("op"), c.get("value"), c.get("field")
    if op == "exists":
        return actual is not None
    if field == "severity" and op in ("gte", "lte"):
        a, w = SEVERITY_RANK.get(str(actual), -1), SEVERITY_RANK.get(str(want), 99)
        return a >= w if op == "gte" else a <= w
    if op == "eq":
        return str(actual) == str(want) if actual is not None else want is None
    if op == "ne":
        return str(actual) != str(want)
    if op in ("in", "not_in"):
        values = [str(v) for v in (want if isinstance(want, list) else str(want).split(","))]
        return (str(actual) in [v.strip() for v in values]) == (op == "in")
    if op == "contains":
        return str(want).lower() in str(actual or "").lower()
    try:
        a, w = float(actual), float(want)
    except (TypeError, ValueError):
        return False
    return a >= w if op == "gte" else a <= w


def first_failing(conditions: list[dict[str, Any]], e: EventRecord) -> dict[str, Any] | None:
    for c in conditions:
        if not _holds(c, _value(e, str(c.get("field")))):
            return c
    return None


def describe_event(e: EventRecord) -> str:
    p = e.payload or {}
    return str(p.get("summary") or p.get("title") or p.get("name") or e.type)


# -- actions -----------------------------------------------------------------------------


def _target(rule: AutomationRule, e: EventRecord | None) -> tuple[str | None, str | None]:
    """The target id for a propose action, or why there is none."""
    action = rule.action
    if action.get("target") == "fixed":
        return str(action.get("target_id")), None
    op = OPERATIONS[action["operation"]]
    want = TARGET_ENTITY.get(op.target_type)
    if e is None or not e.entity_id:
        return None, "The event names no object to act on"
    if e.entity_type != want:
        return None, f"The event is about a {e.entity_type or 'nothing'}, and this operation acts on a {want}"
    return e.entity_id, None


def _reason(rule: AutomationRule, e: EventRecord | None) -> str:
    base = str(rule.action.get("reason") or "").strip()
    why = f"Proposed by automation rule “{rule.name}”" + (f" after {e.type}: {describe_event(e)}" if e else " on its schedule")
    return f"{base}\n\n{why}" if base else why


async def _cooling(db: AsyncSession, rule: AutomationRule, key: str) -> bool:
    if rule.cooldown_minutes <= 0:
        return False
    since = utcnow() - timedelta(minutes=rule.cooldown_minutes)
    n = await db.scalar(
        select(func.count())
        .select_from(AutomationRun)
        .where(
            AutomationRun.rule_id == rule.id,
            AutomationRun.target_key == key,
            AutomationRun.outcome.in_(("proposed", "notified")),
            AutomationRun.at >= since,
        )
    )
    return bool(n)


async def _channels(db: AsyncSession, ids: list[str]) -> list[NotificationChannel]:
    q = select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
    if ids:
        q = q.where(NotificationChannel.id.in_(ids))
    return list(await db.scalars(q))


async def fire(db: AsyncSession, state: AppState, rule: AutomationRule, e: EventRecord | None, trigger: str) -> AutomationRun:
    """Carry out the rule's action once and record the run. Never raises."""
    run = AutomationRun(
        rule_id=rule.id,
        trigger=trigger,
        event_id=e.id if e else None,
        event_type=e.type if e else None,
        outcome="skipped",
        detail="",
    )
    action = rule.action
    try:
        if action.get("kind") == "propose":
            target_id, why = _target(rule, e)
            params = dict(action.get("params") or {})
            if target_id is None:
                run.outcome, run.detail = "skipped", why or ""
            else:
                run.target_key = f"{target_id}:{params.get('port_idx', '')}"[:80]
                if await _cooling(db, rule, run.target_key):
                    run.outcome, run.detail = "skipped", f"Already acted on this target in the last {rule.cooldown_minutes} minutes"
                else:
                    cr = await engine.propose(db, state, rule_actor(rule), action["operation"], target_id, params, _reason(rule, e))
                    run.outcome, run.change_id = "proposed", cr.id
                    run.detail = f"Proposed CHG-{cr.number:04d}: {cr.title} (waits for approval)"
        else:
            if not await state.modules.is_enabled(db, "notifications"):
                run.outcome, run.detail = "skipped", "The Notifications module is off"
            else:
                run.target_key = (e.entity_id if e else "schedule") or "event"
                if await _cooling(db, rule, run.target_key):
                    run.outcome, run.detail = "skipped", f"Already notified about this in the last {rule.cooldown_minutes} minutes"
                else:
                    title = str(action.get("title") or rule.name)
                    body = describe_event(e) if e else f"Scheduled notification from rule “{rule.name}”"
                    severity = str(action.get("severity") or (e.severity if e else "info"))
                    msg = notify.Message(
                        kind="automation",
                        severity=severity,
                        title=title,
                        body=body,
                        data={"rule": rule.name, "event_type": e.type if e else None, "event_id": e.id if e else None},
                    )
                    sent = await notify.route(state, db, list(action.get("channel_ids") or []), msg, None)
                    run.outcome = "notified" if sent else "skipped"
                    run.detail = (
                        f"Sent to {sent} channel{'s' if sent != 1 else ''}" if sent else "No enabled channel at or below this severity"
                    )
    except ChangeRefused as exc:
        run.outcome, run.detail = "refused", str(exc)
    except Exception as exc:  # a broken rule must not stop the others
        run.outcome, run.detail = "error", f"{type(exc).__name__}: {exc}"[:500]
    db.add(run)
    await db.flush()
    if run.outcome in ("proposed", "notified"):
        await audit.record(
            db,
            rule_actor(rule),
            "automation.fire",
            target_type="automation_rule",
            target_id=rule.id,
            detail={"outcome": run.outcome, "event": run.event_type, "change_id": run.change_id, "detail": run.detail},
        )
    return run


async def latest_seq(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.max(EventRecord.seq))) or 0)


async def evaluate(db: AsyncSession, state: AppState) -> dict[str, int]:
    counts = {"rules": 0, "events": 0, "fired": 0}
    now = utcnow()
    for rule in list(await db.scalars(select(AutomationRule).where(AutomationRule.enabled.is_(True)))):
        counts["rules"] += 1
        if rule.trigger_kind == "event":
            q = (
                select(EventRecord)
                .where(EventRecord.type == rule.event_type, EventRecord.seq > rule.cursor_seq)
                .order_by(EventRecord.seq)
                .limit(BATCH_EVENTS)
            )
            for e in list(await db.scalars(q)):
                rule.cursor_seq = e.seq
                counts["events"] += 1
                if e.source == "automation" or first_failing(rule.conditions or [], e) is not None:
                    continue
                await fire(db, state, rule, e, "event")
                rule.last_run_at = now
                counts["fired"] += 1
        elif rule.interval_minutes and (rule.last_run_at is None or now >= rule.last_run_at + timedelta(minutes=rule.interval_minutes)):
            await fire(db, state, rule, None, "schedule")
            rule.last_run_at = now
            counts["fired"] += 1
    return counts


async def evaluate_job(ctx: JobContext) -> dict[str, Any]:
    return await evaluate(ctx.db, ctx.state)


# -- dry run -----------------------------------------------------------------------------


async def _would(db: AsyncSession, rule: AutomationRule, e: EventRecord | None) -> tuple[str, str]:
    action = rule.action
    if action.get("kind") == "propose":
        target_id, why = _target(rule, e)
        if target_id is None:
            return "skipped", why or ""
        try:
            t, title = await engine.preview(db, action["operation"], target_id, dict(action.get("params") or {}))
        except ChangeRefused as exc:
            return "refused", str(exc)
        return "would_propose", f"Would propose “{title}” on {t.label}; it would wait for approval (the live state is read only then)"
    chans = await _channels(db, list(action.get("channel_ids") or []))
    if not chans:
        return "skipped", "No enabled notification channel would receive it"
    return "would_notify", "Would notify " + ", ".join(c.name for c in chans[:5]) + ("…" if len(chans) > 5 else "")


async def dry_run(db: AsyncSession, rule: AutomationRule, days: int = 7) -> dict[str, Any]:
    """What the rule would have done for recent events (or once now, for a schedule). Nothing is stored or sent."""
    out: list[dict[str, Any]] = []
    if rule.trigger_kind == "schedule":
        outcome, detail = await _would(db, rule, None)
        out.append({"event": None, "matched": True, "failed": None, "outcome": outcome, "detail": detail})
        return {"evaluated": 0, "matched": 1, "items": out, "days": days}
    since = utcnow() - timedelta(days=days)
    q = (
        select(EventRecord)
        .where(EventRecord.type == rule.event_type, EventRecord.occurred_at >= since)
        .order_by(EventRecord.seq.desc())
        .limit(DRY_RUN_EVENTS)
    )
    evaluated = matched = 0
    for e in await db.scalars(q):
        evaluated += 1
        failed = first_failing(rule.conditions or [], e)
        item: dict[str, Any] = {
            "event": {
                "id": e.id,
                "type": e.type,
                "occurred_at": e.occurred_at,
                "severity": e.severity,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "summary": describe_event(e),
            },
            "matched": failed is None,
            "failed": failed,
        }
        if failed is None:
            matched += 1
            item["outcome"], item["detail"] = await _would(db, rule, e)
        else:
            item["outcome"], item["detail"] = "no_match", f"{failed.get('field')} {failed.get('op')} {failed.get('value')!r} does not hold"
        out.append(item)
    return {"evaluated": evaluated, "matched": matched, "items": out, "days": days}

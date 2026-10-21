"""Alert evaluation: threshold rules over the metric store.

Each enabled rule is checked against the newest sample of every matching
series. A breach opens a *pending* alert; it *fires* once the breach has lasted
`for_seconds`, and *resolves* when the value recovers, the series goes stale
(no data) or the rule stops covering it. Alerts inside an active maintenance
window are recorded but marked silenced and notify nobody.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import events as ev
from warden import metrics, notify
from warden.db import utcnow
from warden.events import Event
from warden.models import Alert, AlertRule, MaintenanceWindow, MetricSeries, Setting

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

SEEDED_KEY = "monitoring.builtins_seeded"

# key, name, metric, comparator, threshold, for_seconds, severity, description
BUILTIN_RULES: list[tuple[str, str, str, str, float, int, str, str]] = [
    (
        "device_offline",
        "Device offline",
        "device.online",
        "lt",
        1,
        120,
        "critical",
        "The controller has reported the device as not online for 2 minutes.",
    ),
    (
        "controller_unreachable",
        "Controller unreachable",
        "controller.reachable",
        "lt",
        1,
        120,
        "critical",
        "Syncs with the controller have failed for 2 minutes.",
    ),
    ("device_cpu", "High CPU", "device.cpu_pct", "gt", 90, 300, "warning", "CPU above 90% for 5 minutes."),
    ("device_memory", "High memory", "device.mem_pct", "gt", 90, 600, "warning", "Memory above 90% for 10 minutes."),
    ("wifi_retries", "High Wi-Fi retries", "radio.tx_retries_pct", "gt", 15, 600, "warning", "Transmit retries above 15% for 10 minutes."),
    ("probe_loss", "Probe loss", "wan.loss_pct", "gt", 20, 180, "critical", "More than 20% of probe attempts failing for 3 minutes."),
    ("probe_latency", "High latency", "wan.latency_ms", "gt", 150, 300, "warning", "Probe latency above 150 ms for 5 minutes."),
]


async def ensure_builtin_rules(db: AsyncSession) -> None:
    """Create each built-in rule once. A built-in someone deleted is not recreated."""
    row = await db.get(Setting, SEEDED_KEY)
    seeded = set(row.value if row and isinstance(row.value, list) else [])
    missing = [r for r in BUILTIN_RULES if r[0] not in seeded]
    if not missing:
        return
    for key, name, metric, comparator, threshold, for_s, severity, description in missing:
        db.add(
            AlertRule(
                name=name,
                description=description,
                metric=metric,
                comparator=comparator,
                threshold=threshold,
                for_seconds=for_s,
                severity=severity,
                builtin_key=key,
            )
        )
        seeded.add(key)
    if row is None:
        db.add(Setting(key=SEEDED_KEY, value=sorted(seeded)))
    else:
        row.value = sorted(seeded)
    await db.flush()


def breached(rule: AlertRule, value: float) -> bool:
    return value > rule.threshold if rule.comparator == "gt" else value < rule.threshold


def format_value(metric: str, value: float | None) -> str:
    if value is None:
        return "no data"
    unit = metrics.METRICS[metric].unit if metric in metrics.METRICS else ""
    if unit == "bool":
        return "up" if value >= 1 else "down"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "ms":
        return f"{value:.0f} ms"
    if unit == "bps":
        for div, suffix in ((1e9, "Gbps"), (1e6, "Mbps"), (1e3, "kbps")):
            if value >= div:
                return f"{value / div:.1f} {suffix}"
        return f"{value:.0f} bps"
    return f"{value:g}"


def summary(rule: AlertRule, alert: Alert) -> str:
    if metrics.METRICS.get(rule.metric) and metrics.METRICS[rule.metric].unit == "bool":
        return f"{alert.label} is {format_value(rule.metric, alert.value)}"
    op = ">" if rule.comparator == "gt" else "<"
    return f"{alert.label}: {format_value(rule.metric, alert.value)} ({op} {format_value(rule.metric, rule.threshold)})"


def _window_covers(windows: list[MaintenanceWindow], entity_id: str) -> bool:
    return any(not w.entity_ids or entity_id in w.entity_ids for w in windows)


class Evaluator:
    def __init__(self, state: AppState, db: AsyncSession, now: datetime, notify_enabled: bool) -> None:
        self.state, self.db, self.now, self.notify_enabled = state, db, now, notify_enabled
        self.counts = {"pending": 0, "fired": 0, "resolved": 0}

    async def _publish(self, rule: AlertRule, a: Alert, kind: str) -> None:
        await self.state.bus.publish(
            self.db,
            Event(
                type=ev.ALERT_FIRING if kind == "firing" else ev.ALERT_RESOLVED,
                source="monitoring",
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                payload={
                    "alert_id": a.id,
                    "rule": rule.name,
                    "summary": summary(rule, a),
                    "silenced": a.silenced,
                    "resolution": a.resolution,
                },
                severity=a.severity if kind == "firing" else "info",
            ),
        )
        if not self.notify_enabled or a.silenced:
            return
        base = self.state.settings.public_url
        title = f"{rule.name}: {a.label}" if kind == "firing" else f"Resolved: {rule.name} on {a.label}"
        body = (
            summary(rule, a) if kind == "firing" else f"{summary(rule, a)}. Resolved ({(a.resolution or 'recovered').replace('_', ' ')})."
        )
        msg = notify.Message(
            kind=kind,
            severity=a.severity,
            title=title,
            body=body,
            url=f"{base.rstrip('/')}/alerts?alert={a.id}" if base else None,
            data={
                "id": a.id,
                "rule": rule.name,
                "metric": rule.metric,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "label": a.label,
                "value": a.value,
                "threshold": rule.threshold,
                "fired_at": a.fired_at.isoformat() if a.fired_at else None,
                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                "is_demo": a.is_demo,
            },
        )
        await notify.route(self.state, self.db, rule.channel_ids, msg, a.id)

    async def fire(self, rule: AlertRule, a: Alert, windows: list[MaintenanceWindow]) -> None:
        a.state, a.fired_at = "firing", self.now
        a.silenced = _window_covers(windows, a.entity_id)
        await self.db.flush()
        self.counts["fired"] += 1
        await self._publish(rule, a, "firing")

    async def resolve(self, rule: AlertRule, a: Alert, resolution: str) -> None:
        if a.state == "pending":
            await self.db.delete(a)
            return
        a.state, a.resolved_at, a.resolution = "resolved", self.now, resolution
        self.counts["resolved"] += 1
        await self._publish(rule, a, "resolved")

    async def run(self) -> dict[str, Any]:
        db, now = self.db, self.now
        await ensure_builtin_rules(db)
        rules = {r.id: r for r in await db.scalars(select(AlertRule))}
        windows = list(
            await db.scalars(select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at > now))
        )
        open_alerts = {(a.rule_id, a.series_id): a for a in await db.scalars(select(Alert).where(Alert.state.in_(["pending", "firing"])))}
        for rule in rules.values():
            if not rule.enabled:
                continue
            q = select(MetricSeries).where(MetricSeries.metric == rule.metric)
            if rule.entity_ids:
                q = q.where(MetricSeries.entity_id.in_(rule.entity_ids))
            for s in await db.scalars(q):
                a = open_alerts.pop((rule.id, s.id), None)
                if metrics.is_stale(s, now) or s.last_value is None:
                    if a is not None:
                        await self.resolve(rule, a, "no_data")
                    continue
                if not breached(rule, s.last_value):
                    if a is not None:
                        await self.resolve(rule, a, "recovered")
                    continue
                if a is None:
                    a = Alert(
                        rule_id=rule.id,
                        series_id=s.id,
                        entity_type=s.entity_type,
                        entity_id=s.entity_id,
                        label=s.label,
                        state="pending",
                        severity=rule.severity,
                        threshold=rule.threshold,
                        pending_since=now,
                        is_demo=s.is_demo,
                    )
                    db.add(a)
                    self.counts["pending"] += 1
                a.value, a.label, a.severity, a.threshold = s.last_value, s.label, rule.severity, rule.threshold
                if a.state == "pending" and (now - a.pending_since).total_seconds() >= rule.for_seconds:
                    await self.fire(rule, a, windows)
                elif a.state == "firing" and a.silenced and not _window_covers(windows, a.entity_id):
                    a.silenced = False  # window ended while firing; stays quiet until the next transition
        # Whatever is still open is no longer covered: rule disabled, rescoped or deleted series.
        for a in open_alerts.values():
            rule = rules.get(a.rule_id)
            if rule is not None:
                await self.resolve(rule, a, "rule_changed")
        await db.flush()
        return self.counts


async def evaluate(state: AppState, db: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    notify_enabled = await state.modules.is_enabled(db, "notifications")
    return await Evaluator(state, db, now or utcnow(), notify_enabled).run()


async def evaluate_job(ctx: JobContext) -> dict[str, Any]:
    return await evaluate(ctx.state, ctx.db)


async def prune(db: AsyncSession, before: datetime) -> int:
    res = await db.execute(delete(Alert).where(Alert.state == "resolved", Alert.resolved_at < before))
    return res.rowcount or 0

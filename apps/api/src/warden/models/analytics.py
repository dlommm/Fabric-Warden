"""Metric history, dashboards, alerting and notification channels (Phase 3).

Metric samples are stored in three tiers in one table: raw samples from each
sync, 5-minute rollups and 1-hour rollups. Rollups keep avg/min/max/count so a
coarser tier can still show peaks. Retention per tier lives in `warden.metrics`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class MetricSeries(IdMixin, Base):
    """One metric for one entity, e.g. `device.cpu_pct` of the core switch."""

    __tablename__ = "metric_series"
    __table_args__ = (UniqueConstraint("metric", "entity_type", "entity_id"),)

    metric: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))  # device | radio | site | network | controller | probe
    entity_id: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(160))  # display name at the last sample
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_samples_tier_ts", "tier", "ts"),)

    series_id: Mapped[str] = mapped_column(ForeignKey("metric_series.id", ondelete="CASCADE"), primary_key=True)
    tier: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 0 raw | 1 five-minute | 2 hourly
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    avg: Mapped[float] = mapped_column(Float)
    min: Mapped[float] = mapped_column(Float)
    max: Mapped[float] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer, default=1)


class Dashboard(IdMixin, TimestampMixin, Base):
    __tablename__ = "dashboards"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    widgets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    default_range: Mapped[str] = mapped_column(String(8), default="24h")
    template: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AlertRule(IdMixin, TimestampMixin, Base):
    """A threshold on a metric, evaluated per series."""

    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    metric: Mapped[str] = mapped_column(String(64))
    entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = every series of the metric
    comparator: Mapped[str] = mapped_column(String(2))  # gt | lt
    threshold: Mapped[float] = mapped_column(Float)
    for_seconds: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # critical | warning | info
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = every enabled channel
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class Alert(IdMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_rule_series_state", "rule_id", "series_id", "state"),)

    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[str | None] = mapped_column(ForeignKey("metric_series.id", ondelete="SET NULL"), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(16), index=True)  # pending | firing | resolved
    severity: Mapped[str] = mapped_column(String(16))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float] = mapped_column(Float)
    pending_since: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)  # recovered | no_data | rule_changed
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    silenced: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class MaintenanceWindow(IdMixin, TimestampMixin, Base):
    """While active, matching alerts still record but notify nobody."""

    __tablename__ = "maintenance_windows"

    name: Mapped[str] = mapped_column(String(128))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = everything
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class NotificationChannel(IdMixin, TimestampMixin, Base):
    __tablename__ = "notification_channels"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # webhook | ntfy | email
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # non-secret settings only
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_severity: Mapped[str] = mapped_column(String(16), default="warning")
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok | failed
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class NotificationDelivery(IdMixin, Base):
    __tablename__ = "notification_deliveries"

    channel_id: Mapped[str] = mapped_column(ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # firing | resolved | test
    status: Mapped[str] = mapped_column(String(16))  # sent | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

"""Automation Studio: rules that turn events or a schedule into change proposals or notifications.

A rule never writes to a controller. Its only actions are to propose a change
(which then waits for a person to approve and apply it) or to send a
notification through the notifications module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class AutomationRule(IdMixin, TimestampMixin, Base):
    __tablename__ = "automation_rules"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger_kind: Mapped[str] = mapped_column(String(16))  # event | schedule
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{"field": "severity" | "entity_type" | "payload.<key>" ..., "op": "eq" | ..., "value": ...}], all must hold
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # {"kind": "propose", "operation", "target": "event" | "fixed", "target_id", "params", "reason"}
    # or {"kind": "notify", "channel_ids", "severity", "title"}
    action: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)  # per rule and target
    created_by: Mapped[str] = mapped_column(String(64))
    # Events up to this sequence number have been considered; a rule starts from the moment it is enabled.
    cursor_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AutomationRun(IdMixin, Base):
    __tablename__ = "automation_runs"

    rule_id: Mapped[str] = mapped_column(ForeignKey("automation_rules.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # event | schedule | manual
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # proposed | notified | refused | skipped | error
    detail: Mapped[str] = mapped_column(Text, default="")
    change_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True)

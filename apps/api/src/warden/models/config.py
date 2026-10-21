"""Configuration history and compliance.

A snapshot is the controller-reported configuration of one site (networks,
firewall zones and policies, WANs, device firmware) at one sync, stored only
when it differs from the previous snapshot. The official API keeps no change
log, so a diff says what changed between two syncs, never who changed it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class ConfigSnapshot(IdMixin, Base):
    __tablename__ = "config_snapshots"

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Counts of objects per section, for listing without loading content.
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Changes against the previous snapshot of the same site: {section: {added, removed, changed}}.
    change_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(96), default="")
    trigger: Mapped[str] = mapped_column(String(16), default="sync")  # sync | manual
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ComplianceResult(IdMixin, TimestampMixin, Base):
    """The latest evaluation of one built-in control; history lives in the audit and events tables."""

    __tablename__ = "compliance_results"

    control_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # pass | fail | unknown | waived
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    detail: Mapped[str] = mapped_column(Text, default="")
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    failing_since: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # A person can waive a control with a reason and an expiry; the waiver is audited.
    waived_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiver_reason: Mapped[str] = mapped_column(Text, default="")
    waived_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)

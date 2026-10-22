"""Change requests: the only path by which Fabric Warden writes to a controller.

A change request records what would change (the before state read live from
the controller and the expected after state), why, how risky it is, who
proposed and approved it, what was sent, and how the result was verified.
It is never edited after it finishes; a revert is a new change request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime


class ChangeRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "change_requests"

    number: Mapped[int] = mapped_column(Integer, unique=True)  # shown as CHG-0001
    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(48))  # firewall_policy.set_logging | device.restart | port.power_cycle
    target_type: Mapped[str] = mapped_column(String(32))  # firewall_policy | device | port
    target_id: Mapped[str] = mapped_column(String(36), index=True)  # platform id of the policy or device
    target_external_id: Mapped[str] = mapped_column(String(64))
    target_label: Mapped[str] = mapped_column(String(256))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    title: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text, default="")
    # Read live from the controller when proposed; compared again just before applying.
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    impact: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk: Mapped[str] = mapped_column(String(8))  # low | medium | high
    risk_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery: Mapped[str] = mapped_column(Text, default="")
    inverse_of_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True)
    # proposed | approved | applying | verifying | succeeded | failed | reverted | conflict | rejected | cancelled | expired
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    proposed_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decided_note: Mapped[str] = mapped_column(Text, default="")
    applied_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verify_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # What was sent, what came back, every verification read: the change's evidence.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

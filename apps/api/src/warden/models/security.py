"""Log management and security operations.

Log events arrive over syslog (RFC 3164, RFC 5424, optionally carrying a CEF
payload). Cases are the SOC's unit of work: a person opens one from a signal,
links the entities involved and records what was found.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class LogEvent(IdMixin, Base):
    __tablename__ = "log_events"

    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    # When the sender stamped it, if the message carried a parseable timestamp.
    logged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    source_ip: Mapped[str] = mapped_column(String(45), index=True)
    host: Mapped[str] = mapped_column(String(255), default="", index=True)
    app: Mapped[str] = mapped_column(String(128), default="")
    facility: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[int] = mapped_column(Integer, default=6, index=True)  # syslog 0 emergency … 7 debug
    category: Mapped[str] = mapped_column(String(16), default="other", index=True)  # firewall | ids | auth | system | other
    message: Mapped[str] = mapped_column(Text, default="")
    fmt: Mapped[str] = mapped_column(String(8), default="3164")  # 3164 | 5424 | cef | raw
    # CEF extension fields or RFC 5424 structured data.
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Device or client the sender address belongs to, matched at ingest.
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SocCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "soc_cases"

    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(16), default="medium")  # low | medium | high | critical
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | investigating | resolved | false_positive
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    # [{type: client|device|host|ip|policy|log, id, label}]
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The signal key it was opened from, so the signal shows as handled.
    signal_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    # [{at, author, kind: note|status|assign, text}]
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

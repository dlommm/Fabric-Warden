"""Ecosystem integrations: UniFi Protect and Home Assistant (Phase 9).

Both are read-only connections whose credential (a Protect API key or a Home
Assistant long-lived access token) lives in the vault. Observed rows carry the
usual provenance. Matches to network clients are computed when read, from the
current inventory, so they never go stale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime
from warden.models.inventory import ObservedMixin


class IntegrationConnection(IdMixin, TimestampMixin, Base):
    __tablename__ = "integration_connections"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24), index=True)  # unifi-protect | home-assistant
    base_url: Mapped[str] = mapped_column(String(512))
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    ca_bundle_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | degraded | unreachable | unknown
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ProtectDevice(IdMixin, ObservedMixin, Base):
    """A camera or an NVR as the Protect Integration API reports it."""

    __tablename__ = "protect_devices"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    connection_id: Mapped[str] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # camera | nvr
    name: Mapped[str] = mapped_column(String(128))
    model_key: Mapped[str] = mapped_column(String(32), default="")
    type: Mapped[str] = mapped_column(String(64), default="")  # hardware model, e.g. UVC G5 Bullet, UNVR
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="")  # CONNECTED | CONNECTING | DISCONNECTED (cameras)
    # Camera feature flags / smart detection; NVR arm mode. Only published fields.
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class HaEntity(IdMixin, ObservedMixin, Base):
    """A selected Home Assistant entity: a UPS or temperature sensor, a router tracker, a connectivity sensor.

    Only allow-listed attributes are kept; GPS trackers are not stored, so no location is collected.
    """

    __tablename__ = "ha_entities"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    connection_id: Mapped[str] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16), index=True)  # ups | temperature | tracker | connectivity
    category_basis: Mapped[str] = mapped_column(String(16), default="device_class")  # device_class | domain | name
    name: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(255), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    device_class: Mapped[str] = mapped_column(String(64), default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_changed: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

"""Controller connections and the observed network inventory.

Every observed row carries provenance: which collector produced it, when it was
last observed, and whether it came from demo fixtures. Stable identities are the
platform `id`; controller identifiers are kept in `external_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class Controller(IdMixin, TimestampMixin, Base):
    __tablename__ = "controllers"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # unifi-network | unifi-simulator
    base_url: Mapped[str] = mapped_column(String(512))
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    ca_bundle_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    # read_only is the default; writes additionally require the change engine.
    write_mode: Mapped[str] = mapped_column(String(16), default="read_only")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | degraded | unreachable | unknown
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    application_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ObservedMixin:
    external_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(96))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # False once a sync no longer reports the object; history is retained.
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Site(IdMixin, ObservedMixin, Base):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("controller_id", "external_id"),)

    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    internal_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Device(IdMixin, ObservedMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(64))
    mac_address: Mapped[str] = mapped_column(String(17), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware_updatable: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32))
    features: Mapped[list[str]] = mapped_column(JSON, default=list)
    uplink_device_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stats_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # User-owned metadata; never overwritten by discovery.
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class Port(IdMixin, Base):
    __tablename__ = "ports"
    __table_args__ = (UniqueConstraint("device_id", "idx"),)

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    connector: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    speed_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_speed_mbps: Mapped[int] = mapped_column(Integer)
    poe: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Radio(IdMixin, Base):
    __tablename__ = "radios"
    __table_args__ = (UniqueConstraint("device_id", "frequency_ghz"),)

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    frequency_ghz: Mapped[float] = mapped_column()
    channel: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_width_mhz: Mapped[int] = mapped_column(Integer)
    wlan_standard: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Client(IdMixin, ObservedMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(16))  # WIRED | WIRELESS | VPN | TELEPORT
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    uplink_device_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    access: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)


class Network(IdMixin, ObservedMixin, Base):
    __tablename__ = "networks"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    management: Mapped[str] = mapped_column(String(16))  # GATEWAY | SWITCH | UNMANAGED
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    zone_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ipv4: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Wan(IdMixin, ObservedMixin, Base):
    __tablename__ = "wans"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))


class FirewallZone(IdMixin, ObservedMixin, Base):
    __tablename__ = "firewall_zones"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network_external_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class FirewallPolicy(IdMixin, ObservedMixin, Base):
    __tablename__ = "firewall_policies"
    __table_args__ = (UniqueConstraint("site_id", "external_id"),)

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    action: Mapped[str] = mapped_column(String(16))
    index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_zone_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination_zone_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)

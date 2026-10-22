"""SNMP interface counters: read-only IF-MIB polling of switches and gateways.

A profile holds one set of SNMP credentials (UniFi applies one SNMP user to a
whole site), with the secrets in the credential vault. A target is one agent
to poll, usually linked to an observed UniFi device. Interfaces are observed
rows carrying the last raw counters and the rates derived from the previous
poll; history goes to the metric store.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime
from warden.models.inventory import ObservedMixin


class SnmpProfile(IdMixin, TimestampMixin, Base):
    __tablename__ = "snmp_profiles"

    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(8))  # v3 | v2c
    # v3 only; the auth and privacy passphrases (or the v2c community) are in the vault as JSON.
    username: Mapped[str] = mapped_column(String(64), default="")
    auth_protocol: Mapped[str] = mapped_column(String(16), default="SHA")  # MD5 | SHA | SHA-224 | SHA-256 | SHA-384 | SHA-512 | none
    priv_protocol: Mapped[str] = mapped_column(String(16), default="AES")  # DES | AES | AES-192 | AES-256 | none
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SnmpTarget(IdMixin, TimestampMixin, Base):
    __tablename__ = "snmp_targets"

    profile_id: Mapped[str] = mapped_column(ForeignKey("snmp_profiles.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=161)
    # The observed UniFi device this agent belongs to, when known.
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | degraded | unreachable | unknown
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    error_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # auth | unreachable | protocol | error
    sys_name: Mapped[str] = mapped_column(String(255), default="")
    sys_descr: Mapped[str] = mapped_column(String(512), default="")
    sys_uptime_s: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SnmpInterface(IdMixin, ObservedMixin, Base):
    """One IF-MIB interface row. `external_id` is the ifIndex."""

    __tablename__ = "snmp_interfaces"
    __table_args__ = (UniqueConstraint("target_id", "external_id"),)

    target_id: Mapped[str] = mapped_column(ForeignKey("snmp_targets.id", ondelete="CASCADE"), index=True)
    if_index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), default="")  # ifName
    descr: Mapped[str] = mapped_column(String(255), default="")  # ifDescr
    alias: Mapped[str] = mapped_column(String(255), default="")  # ifAlias
    if_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    admin_up: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    oper_status: Mapped[str] = mapped_column(String(16), default="unknown")  # up | down | testing | dormant | notPresent | ...
    speed_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ifHighSpeed
    # UniFi port number this interface was matched to from its name; a heuristic, labelled as such.
    port_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Last raw counters (ifHC*Octets and the 32-bit error/discard counters) and when they were read.
    counters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    counters_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    rx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Errors and discards (in + out) per minute over the last poll interval.
    errors_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    discards_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)

"""Servers & virtualization: hypervisor, NAS, container and Linux host inventory.

Connections are read-only API credentials for Proxmox VE, Unraid, the Docker
Engine and node_exporter. Hosts, workloads (VMs, containers) and storage are
observed rows with the same provenance fields as the network inventory.
Each observed host and workload may be matched to a UniFi client by address
or MAC, which is what joins the server view to the network view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime
from warden.models.inventory import ObservedMixin


class HostConnection(IdMixin, TimestampMixin, Base):
    __tablename__ = "host_connections"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # proxmox | unraid | docker | node_exporter
    base_url: Mapped[str] = mapped_column(String(512))
    # Proxmox: "USER@REALM!TOKENID" with the secret in the vault. Unraid: API key in the vault.
    # Docker and node_exporter usually need no credential.
    token_id: Mapped[str] = mapped_column(String(128), default="")
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    ca_bundle_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | degraded | unreachable | unknown
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Host(IdMixin, ObservedMixin, Base):
    """A machine: a Proxmox node, an Unraid server, a Docker host or a Linux host."""

    __tablename__ = "hosts"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    connection_id: Mapped[str] = mapped_column(ForeignKey("host_connections.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # proxmox-node | unraid | docker | linux
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # online | offline | unknown
    hostname: Mapped[str] = mapped_column(String(255), default="")
    os: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    mac_addresses: Mapped[list[str]] = mapped_column(JSON, default=list)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mem_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    load_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_s: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Kind-specific facts (Unraid array state and UPS, Proxmox cluster, CPU counters).
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Matched UniFi client (by IP, then MAC, then hostname); recomputed every sync.
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    match_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ip | mac | hostname
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class Workload(IdMixin, ObservedMixin, Base):
    """A VM, LXC container or Docker container running on a host."""

    __tablename__ = "workloads"
    __table_args__ = (UniqueConstraint("host_id", "external_id"),)

    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # vm | lxc | container
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))  # running | stopped | paused | other
    raw_status: Mapped[str] = mapped_column(String(64), default="")
    vmid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_count: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mem_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disk_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uptime_s: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ip_addresses: Mapped[list[str]] = mapped_column(JSON, default=list)
    mac_addresses: Mapped[list[str]] = mapped_column(JSON, default=list)
    image: Mapped[str] = mapped_column(String(255), default="")
    ports: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    autostart: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    template: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    match_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)


class HostStorage(IdMixin, ObservedMixin, Base):
    """A datastore, pool, array, disk or filesystem a host reports."""

    __tablename__ = "host_storage"
    __table_args__ = (UniqueConstraint("host_id", "external_id"),)

    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # datastore | array | disk | pool | filesystem
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    healthy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

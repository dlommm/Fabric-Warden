"""IPAM documentation: the planned side of planned-vs-observed.

These rows are owned by people, not by discovery. Sync never writes them; the
IPAM API compares them against observed networks and clients. Nothing here is
pushed to a controller.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin


class IpPrefix(IdMixin, TimestampMixin, Base):
    __tablename__ = "ip_prefixes"

    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    cidr: Mapped[str] = mapped_column(String(49), index=True)  # normalised network form
    family: Mapped[int] = mapped_column(Integer)  # 4 | 6
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | planned | reserved | deprecated
    description: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Set when the record was adopted from a demo network.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class IpReservation(IdMixin, TimestampMixin, Base):
    __tablename__ = "ip_reservations"
    __table_args__ = (UniqueConstraint("prefix_id", "address"),)

    prefix_id: Mapped[str] = mapped_column(ForeignKey("ip_prefixes.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(45), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="static")  # static | dhcp | infrastructure
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class VlanRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "vlan_records"

    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    vlan_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | planned | reserved | deprecated
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

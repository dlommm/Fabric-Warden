"""Physical infrastructure documentation: locations, racks, cabling, power, assets.

Like IPAM, these rows are owned by people. Discovery never writes them; the
APIs compare them against observed inventory where a link exists (a rack item
linked to a device, a cable end on a device port). Nothing here reaches a
controller.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class Location(IdMixin, TimestampMixin, Base):
    """A place racks live in: site → building → room → row, nested freely."""

    __tablename__ = "dcim_locations"

    parent_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_locations.id", ondelete="CASCADE"), nullable=True, index=True)
    # Optional link to an observed controller site.
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="room")  # site | building | floor | room | row | cage
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Rack(IdMixin, TimestampMixin, Base):
    __tablename__ = "dcim_racks"

    location_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_locations.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    u_height: Mapped[int] = mapped_column(Integer, default=42)
    # Units numbered from the bottom (U1 at the bottom) unless descending.
    descending_units: Mapped[bool] = mapped_column(Boolean, default=False)
    width_in: Mapped[int] = mapped_column(Integer, default=19)  # 10 | 19 | 23
    depth_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | planned | retired
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class RackItem(IdMixin, TimestampMixin, Base):
    """Anything mounted in (or zero-U on) a rack: devices, panels, power, shelves."""

    __tablename__ = "dcim_rack_items"

    rack_id: Mapped[str] = mapped_column(ForeignKey("dcim_racks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    # device | server | patch_panel | pdu | ups | shelf | cable_manager | blank | other
    kind: Mapped[str] = mapped_column(String(16), default="device")
    # Lowest occupied unit (rack numbering); null = zero-U / side-mounted.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_u: Mapped[int] = mapped_column(Integer, default=1)
    face: Mapped[str] = mapped_column(String(8), default="front")  # front | rear
    full_depth: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="installed")  # installed | planned | removed
    # Optional link to an observed device (one rack slot per device).
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, unique=True)
    manufacturer: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    serial: Mapped[str] = mapped_column(String(64), default="")
    # Patch panels: numbered ports, front and rear sides pass straight through.
    port_count: Mapped[int] = mapped_column(Integer, default=0)
    connector: Mapped[str] = mapped_column(String(16), default="")  # RJ45 | LC | SC | MPO | ...
    # Power: typical draw for loads; usable capacity and outlets for UPS/PDU.
    power_draw_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_capacity_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outlet_count: Mapped[int] = mapped_column(Integer, default=0)
    # UPS: runtime at full rated load, used for a linear runtime estimate.
    runtime_full_load_min: Mapped[float | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Cable(IdMixin, TimestampMixin, Base):
    __tablename__ = "dcim_cables"

    label: Mapped[str] = mapped_column(String(64), unique=True)
    # cat5e | cat6 | cat6a | cat8 | om3 | om4 | os2 | dac | aoc | power | other
    type: Mapped[str] = mapped_column(String(16), default="cat6")
    color: Mapped[str] = mapped_column(String(32), default="")
    length_m: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="installed")  # planned | installed | decommissioned
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CableEnd(IdMixin, Base):
    """One termination of a cable.

    kind=device_port: an observed device's port (device_id + port_idx).
    kind=panel_port: a patch panel port (item_id + port_idx + side).
    kind=item: a rack item without modelled ports (a server NIC, a camera).
    kind=external: free text (wall jack, ISP handoff).
    """

    __tablename__ = "dcim_cable_ends"
    __table_args__ = (UniqueConstraint("cable_id", "end"),)

    cable_id: Mapped[str] = mapped_column(ForeignKey("dcim_cables.id", ondelete="CASCADE"), index=True)
    end: Mapped[str] = mapped_column(String(1))  # a | b
    kind: Mapped[str] = mapped_column(String(16))
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_rack_items.id", ondelete="SET NULL"), nullable=True, index=True)
    port_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)  # front | rear (panels)
    text: Mapped[str] = mapped_column(String(128), default="")


class PowerFeed(IdMixin, TimestampMixin, Base):
    """A utility circuit feeding UPSes or PDUs."""

    __tablename__ = "dcim_power_feeds"

    location_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_locations.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    voltage_v: Mapped[int] = mapped_column(Integer, default=230)
    amperage_a: Mapped[int] = mapped_column(Integer, default=16)
    # Continuous-load derating (80 % is the common breaker rule).
    max_utilisation_pct: Mapped[int] = mapped_column(Integer, default=80)
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class PowerLink(IdMixin, TimestampMixin, Base):
    """source (a feed or a UPS/PDU item) → target rack item."""

    __tablename__ = "dcim_power_links"

    source_feed_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_power_feeds.id", ondelete="CASCADE"), nullable=True, index=True)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_rack_items.id", ondelete="CASCADE"), nullable=True, index=True)
    source_outlet: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_item_id: Mapped[str] = mapped_column(ForeignKey("dcim_rack_items.id", ondelete="CASCADE"), index=True)
    # Which power supply on the target (A/B for redundant PSUs).
    target_input: Mapped[str] = mapped_column(String(8), default="A")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Asset(IdMixin, TimestampMixin, Base):
    """Lifecycle record: hardware, spares, licences and subscriptions."""

    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="hardware")  # hardware | spare | licence | subscription | consumable
    # ordered | in_service | spare | in_repair | retired | disposed
    status: Mapped[str] = mapped_column(String(16), default="in_service")
    asset_tag: Mapped[str] = mapped_column(String(64), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    serial: Mapped[str] = mapped_column(String(64), default="", index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # Spares: raise a finding when quantity falls below this.
    min_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier: Mapped[str] = mapped_column(String(128), default="")
    order_ref: Mapped[str] = mapped_column(String(64), default="")
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="")
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    support_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_of_sale: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_of_life: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Licences and subscriptions. Keys belong in the credential vault, not here.
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    rack_item_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_rack_items.id", ondelete="SET NULL"), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("dcim_locations.id", ondelete="SET NULL"), nullable=True, index=True)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DocPage(IdMixin, TimestampMixin, Base):
    """A Markdown page. Every save appends a DocVersion; the page holds the head."""

    __tablename__ = "doc_pages"

    slug: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("doc_pages.id", ondelete="SET NULL"), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DocVersion(IdMixin, Base):
    __tablename__ = "doc_versions"
    __table_args__ = (UniqueConstraint("page_id", "version"),)

    page_id: Mapped[str] = mapped_column(ForeignKey("doc_pages.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(String(200), default="")
    author: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

"""phase 2 client history ipam assets

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 10:09:29.991559
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import warden.db

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ip_prefixes",
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("cidr", sa.String(length=49), nullable=False),
        sa.Column("family", sa.Integer(), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("fk_ip_prefixes_site_id_sites"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ip_prefixes")),
    )
    with op.batch_alter_table("ip_prefixes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ip_prefixes_cidr"), ["cidr"], unique=False)
        batch_op.create_index(batch_op.f("ix_ip_prefixes_site_id"), ["site_id"], unique=False)

    op.create_table(
        "vlan_records",
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("vlan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("fk_vlan_records_site_id_sites"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vlan_records")),
    )
    with op.batch_alter_table("vlan_records", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_vlan_records_site_id"), ["site_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_vlan_records_vlan_id"), ["vlan_id"], unique=False)

    op.create_table(
        "client_sightings",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("uplink_device_external_id", sa.String(length=64), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_client_sightings_client_id_clients"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_sightings")),
    )
    with op.batch_alter_table("client_sightings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_client_sightings_client_id"), ["client_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_client_sightings_ip_address"), ["ip_address"], unique=False)

    op.create_table(
        "ip_reservations",
        sa.Column("prefix_id", sa.String(length=36), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mac_address", sa.String(length=17), nullable=True),
        sa.Column("hostname", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", warden.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["prefix_id"], ["ip_prefixes.id"], name=op.f("fk_ip_reservations_prefix_id_ip_prefixes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ip_reservations")),
        sa.UniqueConstraint("prefix_id", "address", name=op.f("uq_ip_reservations_prefix_id")),
    )
    with op.batch_alter_table("ip_reservations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ip_reservations_address"), ["address"], unique=False)
        batch_op.create_index(batch_op.f("ix_ip_reservations_prefix_id"), ["prefix_id"], unique=False)

    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alias", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("owner", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")))

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("asset", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_column("asset")

    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.drop_column("notes")
        batch_op.drop_column("owner")
        batch_op.drop_column("alias")

    with op.batch_alter_table("ip_reservations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ip_reservations_prefix_id"))
        batch_op.drop_index(batch_op.f("ix_ip_reservations_address"))

    op.drop_table("ip_reservations")
    with op.batch_alter_table("client_sightings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_client_sightings_ip_address"))
        batch_op.drop_index(batch_op.f("ix_client_sightings_client_id"))

    op.drop_table("client_sightings")
    with op.batch_alter_table("vlan_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_vlan_records_vlan_id"))
        batch_op.drop_index(batch_op.f("ix_vlan_records_site_id"))

    op.drop_table("vlan_records")
    with op.batch_alter_table("ip_prefixes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ip_prefixes_site_id"))
        batch_op.drop_index(batch_op.f("ix_ip_prefixes_cidr"))

    op.drop_table("ip_prefixes")

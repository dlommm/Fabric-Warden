"""Traffic flows: NetFlow/IPFIX records rolled up per minute and conversation.

Individual flow records are not kept. The collector sums them per minute,
exporter, source, destination, protocol and service port, which keeps a home
network's traffic history to a manageable number of rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, UTCDateTime


class FlowRollup(IdMixin, Base):
    __tablename__ = "flow_rollups"
    __table_args__ = (Index("ix_flow_rollups_bucket_src", "bucket", "src_ip"), Index("ix_flow_rollups_bucket_dst", "bucket", "dst_ip"))

    # Start of the minute the records arrived in (demo rows carry their synthetic time).
    bucket: Mapped[datetime] = mapped_column(UTCDateTime())
    exporter_ip: Mapped[str] = mapped_column(String(45))
    # 5, 9 or 10 (IPFIX).
    version: Mapped[int] = mapped_column(Integer)
    ip_version: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[int] = mapped_column(Integer)
    src_ip: Mapped[str] = mapped_column(String(45))
    dst_ip: Mapped[str] = mapped_column(String(45))
    # The lower of the two transport ports, read as the service port (a heuristic); null for ICMP and others.
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Counts as exported, before any sampling scale-up.
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    packets: Mapped[int] = mapped_column(BigInteger, default=0)
    flows: Mapped[int] = mapped_column(Integer, default=0)
    # 1 in N packets sampled; 1 = unsampled, 0 = the exporter did not say (counted as unsampled).
    sampling: Mapped[int] = mapped_column(Integer, default=0)
    src_client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    dst_client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    src_device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    dst_device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

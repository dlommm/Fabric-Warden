"""Flow collector: decode NetFlow v5, v9 and IPFIX, roll records up per minute, match to inventory.

Wire formats follow the published specifications: NetFlow v5 (Cisco's fixed
48-byte record), NetFlow v9 (RFC 3954) and IPFIX (RFC 7011, version 10), with
information element numbers from the IANA IPFIX registry. v9 and IPFIX are
template based: data records arriving before their template cannot be decoded
and are counted, not guessed. NetFlow v5 carries IPv4 only.

Sampling: v5 reports its interval in the header; v9 and IPFIX report it in
options records (samplingInterval, samplerRandomInterval, samplingPacketInterval
and samplingPacketSpace) or on the flow record itself. When an exporter reports
nothing, WARDEN_FLOW_SAMPLING_RATE is assumed if set; otherwise the counts are
stored as exported and labelled as possibly sampled.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from warden.db import utcnow
from warden.models import Client, Device, FlowRollup

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

log = logging.getLogger(__name__)

# IANA IPFIX information elements (NetFlow v9 field types share the numbers below 128).
IE_OCTETS, IE_PACKETS, IE_PROTO, IE_TCP_FLAGS = 1, 2, 4, 6
IE_SRC_PORT, IE_SRC_V4, IE_DST_PORT, IE_DST_V4 = 7, 8, 11, 12
IE_OUT_BYTES, IE_OUT_PKTS = 23, 24  # NetFlow v9 OUT_BYTES / OUT_PKTS
IE_SRC_V6, IE_DST_V6 = 27, 28
IE_SAMPLING_INTERVAL, IE_SAMPLER_RANDOM_INTERVAL = 34, 50
IE_PACKET_INTERVAL, IE_PACKET_SPACE = 305, 306
ADDRESS_IES = {8: 4, 12: 4, 15: 4, 18: 4, 225: 4, 226: 4, 27: 16, 28: 16, 62: 16, 63: 16, 281: 16, 282: 16}
IE_NAMES = {
    1: "octetDeltaCount",
    2: "packetDeltaCount",
    4: "protocolIdentifier",
    5: "ipClassOfService",
    6: "tcpControlFlags",
    7: "sourceTransportPort",
    8: "sourceIPv4Address",
    10: "ingressInterface",
    11: "destinationTransportPort",
    12: "destinationIPv4Address",
    14: "egressInterface",
    15: "ipNextHopIPv4Address",
    21: "flowEndSysUpTime",
    22: "flowStartSysUpTime",
    23: "postOctetDeltaCount",
    24: "postPacketDeltaCount",
    27: "sourceIPv6Address",
    28: "destinationIPv6Address",
    32: "icmpTypeCodeIPv4",
    34: "samplingInterval",
    35: "samplingAlgorithm",
    48: "samplerId",
    49: "samplerMode",
    50: "samplerRandomInterval",
    56: "sourceMacAddress",
    58: "vlanId",
    60: "ipVersion",
    61: "flowDirection",
    62: "ipNextHopIPv6Address",
    80: "destinationMacAddress",
    136: "flowEndReason",
    139: "icmpTypeCodeIPv6",
    150: "flowStartSeconds",
    151: "flowEndSeconds",
    152: "flowStartMilliseconds",
    153: "flowEndMilliseconds",
    160: "systemInitTimeMilliseconds",
    225: "postNATSourceIPv4Address",
    226: "postNATDestinationIPv4Address",
    227: "postNAPTSourceTransportPort",
    228: "postNAPTDestinationTransportPort",
    230: "natEvent",
    281: "postNATSourceIPv6Address",
    282: "postNATDestinationIPv6Address",
    305: "samplingPacketInterval",
    306: "samplingPacketSpace",
}
PORTED = {6, 17, 132}  # TCP, UDP, SCTP
PROTOCOLS = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP", 58: "ICMPv6", 132: "SCTP"}
V5_HEADER = struct.Struct("!HHIIIIBBH")
V5_RECORD = struct.Struct("!4s4s4sHHIIIIHHxBBBHHBBxx")
V9_HEADER = struct.Struct("!HHIIII")
IPFIX_HEADER = struct.Struct("!HHIII")
MAX_TEMPLATES = 2048
MAX_FIELDS = 256
PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16", "fc00::/7", "fe80::/10")
]


class FlowDecodeError(ValueError):
    pass


@dataclass
class Flow:
    src: str
    dst: str
    protocol: int
    src_port: int = 0
    dst_port: int = 0
    bytes: int = 0
    packets: int = 0
    sampling: int = 0

    @property
    def ip_version(self) -> int:
        return 6 if ":" in self.src else 4

    @property
    def port(self) -> int | None:
        """The lower port is usually the service; ephemeral client ports are high. A heuristic."""
        if self.protocol not in PORTED:
            return None
        ports = [p for p in (self.src_port, self.dst_port) if p]
        return min(ports) if ports else None


@dataclass
class Template:
    # (information element, length, enterprise number or 0); length 65535 means variable (IPFIX).
    fields: list[tuple[int, int, int]]
    scope_count: int = 0  # options templates: how many leading fields are scope fields

    @property
    def min_length(self) -> int:
        return sum(1 if ln == 65535 else ln for _ie, ln, _pen in self.fields)


@dataclass
class Exporter:
    """What one exporter (address + observation domain) has told us: templates, sampling, versions."""

    address: str
    domain: int
    version: int = 0
    templates: dict[int, Template] = field(default_factory=dict)
    sampling: int = 0
    packets: int = 0
    flows: int = 0
    undecoded: int = 0
    last_at: datetime | None = None

    def fields_seen(self) -> list[str]:
        ies = sorted({ie for t in self.templates.values() if not t.scope_count for ie, _ln, pen in t.fields if not pen})
        return [IE_NAMES.get(ie, f"ie{ie}") for ie in ies]


def scope(address: str) -> str:
    """private (RFC 1918, CGNAT, ULA, link-local) or public; documentation ranges count as public."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "public"
    return "private" if any(ip in n for n in PRIVATE_NETS if n.version == ip.version) else "public"


def _uint(b: bytes) -> int:
    return int.from_bytes(b, "big")


class Decoder:
    """Stateful decoder: v9 and IPFIX templates are remembered per exporter and observation domain."""

    def __init__(self, default_sampling: int = 0) -> None:
        self.exporters: dict[tuple[str, int], Exporter] = {}
        self.default_sampling = default_sampling

    def _exporter(self, address: str, domain: int, version: int) -> Exporter:
        key = (address, domain)
        ex = self.exporters.get(key)
        if ex is None:
            if len(self.exporters) >= 256:
                raise FlowDecodeError("too many exporters")
            ex = self.exporters[key] = Exporter(address, domain)
        ex.version, ex.last_at = version, utcnow()
        ex.packets += 1
        return ex

    def decode(self, data: bytes, address: str) -> list[Flow]:
        if len(data) < 2:
            raise FlowDecodeError("datagram too short")
        version = struct.unpack_from("!H", data)[0]
        if version == 5:
            return self._v5(data, address)
        if version == 9:
            return self._v9(data, address)
        if version == 10:
            return self._ipfix(data, address)
        raise FlowDecodeError(f"unsupported NetFlow version {version}")

    # NetFlow v5: fixed header and records, IPv4 only.
    def _v5(self, data: bytes, address: str) -> list[Flow]:
        if len(data) < V5_HEADER.size:
            raise FlowDecodeError("short v5 header")
        _v, count, _up, _secs, _nsecs, _seq, _etype, engine_id, sampling = V5_HEADER.unpack_from(data)
        if count > 30 or len(data) < V5_HEADER.size + count * V5_RECORD.size:
            raise FlowDecodeError("v5 record count does not match length")
        ex = self._exporter(address, engine_id, 5)
        # Top two bits are the sampling mode, the rest the interval; 0 means the exporter did not say.
        interval = sampling & 0x3FFF
        ex.sampling = interval if interval > 1 else 0
        rate = ex.sampling or self.default_sampling
        out = []
        for i in range(count):
            src, dst, _hop, _in, _out, pkts, octets, _first, _last, sport, dport, _flags, proto, _tos, *_ = V5_RECORD.unpack_from(
                data, V5_HEADER.size + i * V5_RECORD.size
            )
            out.append(Flow(str(ipaddress.IPv4Address(src)), str(ipaddress.IPv4Address(dst)), proto, sport, dport, octets, pkts, rate))
        ex.flows += len(out)
        return out

    # NetFlow v9 (RFC 3954): flowset 0 = templates, 1 = options templates, >= 256 = data.
    def _v9(self, data: bytes, address: str) -> list[Flow]:
        if len(data) < V9_HEADER.size:
            raise FlowDecodeError("short v9 header")
        _v, _count, _up, _secs, _seq, source_id = V9_HEADER.unpack_from(data)
        ex = self._exporter(address, source_id, 9)
        return self._sets(ex, data, V9_HEADER.size, len(data), template_ids=(0, 1), ipfix=False)

    # IPFIX (RFC 7011): set 2 = templates, 3 = options templates, >= 256 = data.
    def _ipfix(self, data: bytes, address: str) -> list[Flow]:
        if len(data) < IPFIX_HEADER.size:
            raise FlowDecodeError("short IPFIX header")
        _v, length, _export, _seq, domain = IPFIX_HEADER.unpack_from(data)
        if length > len(data) or length < IPFIX_HEADER.size:
            raise FlowDecodeError("IPFIX length does not match datagram")
        ex = self._exporter(address, domain, 10)
        return self._sets(ex, data, IPFIX_HEADER.size, length, template_ids=(2, 3), ipfix=True)

    def _sets(self, ex: Exporter, data: bytes, pos: int, end: int, *, template_ids: tuple[int, int], ipfix: bool) -> list[Flow]:
        out: list[Flow] = []
        while pos + 4 <= end:
            set_id, set_len = struct.unpack_from("!HH", data, pos)
            if set_len < 4 or pos + set_len > end:
                raise FlowDecodeError("set length out of range")
            body, body_end = pos + 4, pos + set_len
            if set_id == template_ids[0]:
                self._templates(ex, data, body, body_end, ipfix=ipfix, options=False)
            elif set_id == template_ids[1]:
                self._templates(ex, data, body, body_end, ipfix=ipfix, options=True)
            elif set_id >= 256:
                out += self._data(ex, set_id, data, body, body_end)
            pos = body_end
        ex.flows += len(out)
        return out

    def _templates(self, ex: Exporter, data: bytes, pos: int, end: int, *, ipfix: bool, options: bool) -> None:
        while pos + 4 <= end:
            if options and not ipfix:
                # v9 options template: id, scope length (bytes), option length (bytes), then (type, length) pairs.
                if pos + 6 > end:
                    return
                tid, scope_len, opt_len = struct.unpack_from("!HHH", data, pos)
                pos += 6
                n_scope, count = scope_len // 4, (scope_len + opt_len) // 4
            else:
                tid, count = struct.unpack_from("!HH", data, pos)
                pos += 4
                n_scope = 0
                if options:
                    if pos + 2 > end:
                        return
                    n_scope = struct.unpack_from("!H", data, pos)[0]
                    pos += 2
            if tid < 256:
                return  # padding, or a malformed set
            if count == 0:
                ex.templates.pop(tid, None)  # IPFIX template withdrawal
                continue
            if count > MAX_FIELDS:
                raise FlowDecodeError("template has too many fields")
            fields = []
            for _ in range(count):
                if pos + 4 > end:
                    raise FlowDecodeError("truncated template")
                ie, ln = struct.unpack_from("!HH", data, pos)
                pos += 4
                pen = 0
                if ipfix and ie & 0x8000:
                    if pos + 4 > end:
                        raise FlowDecodeError("truncated enterprise field")
                    pen = struct.unpack_from("!I", data, pos)[0]
                    pos += 4
                    ie &= 0x7FFF
                fields.append((ie, ln, pen))
            if tid not in ex.templates and len(ex.templates) >= MAX_TEMPLATES:
                raise FlowDecodeError("too many templates")
            t = Template(fields, n_scope)
            if t.min_length == 0:
                raise FlowDecodeError("template with zero-length records")
            ex.templates[tid] = t

    def _data(self, ex: Exporter, tid: int, data: bytes, pos: int, end: int) -> list[Flow]:
        t = ex.templates.get(tid)
        if t is None:
            ex.undecoded += 1
            return []
        out = []
        while end - pos >= t.min_length:
            values: dict[int, bytes] = {}
            for ie, ln, pen in t.fields:
                if ln == 65535:
                    ln = data[pos]
                    pos += 1
                    if ln == 255:
                        ln = struct.unpack_from("!H", data, pos)[0]
                        pos += 2
                if pos + ln > end:
                    raise FlowDecodeError("truncated data record")
                if not pen:
                    values[ie] = data[pos : pos + ln]
                pos += ln
            if t.scope_count:
                self._options(ex, values)
            elif flow := self._flow(ex, values):
                out.append(flow)
        return out

    @staticmethod
    def _rate(values: dict[int, bytes]) -> int:
        for ie in (IE_SAMPLING_INTERVAL, IE_SAMPLER_RANDOM_INTERVAL):
            if ie in values and (n := _uint(values[ie])):
                return n
        if IE_PACKET_INTERVAL in values and (sel := _uint(values[IE_PACKET_INTERVAL])):
            return max(1, round((sel + _uint(values.get(IE_PACKET_SPACE, b""))) / sel))
        return 0

    def _options(self, ex: Exporter, values: dict[int, bytes]) -> None:
        if rate := self._rate(values):
            ex.sampling = rate

    def _flow(self, ex: Exporter, values: dict[int, bytes]) -> Flow | None:
        src = values.get(IE_SRC_V4) or values.get(IE_SRC_V6)
        dst = values.get(IE_DST_V4) or values.get(IE_DST_V6)
        if not src or not dst or len(src) not in (4, 16) or len(src) != len(dst):
            return None
        octets = values.get(IE_OCTETS) or values.get(IE_OUT_BYTES) or b""
        pkts = values.get(IE_PACKETS) or values.get(IE_OUT_PKTS) or b""
        return Flow(
            src=str(ipaddress.ip_address(src)),
            dst=str(ipaddress.ip_address(dst)),
            protocol=_uint(values.get(IE_PROTO, b"")),
            src_port=_uint(values.get(IE_SRC_PORT, b"")),
            dst_port=_uint(values.get(IE_DST_PORT, b"")),
            bytes=_uint(octets),
            packets=_uint(pkts),
            sampling=self._rate(values) or ex.sampling or self.default_sampling,
        )


# --- Encoders: used to build demo traffic and test datagrams that go through the real decoder. ---


def encode_v5(flows: list[Flow], *, sampling: int = 0, engine_id: int = 0, sequence: int = 0) -> bytes:
    now = int(datetime.now(UTC).timestamp())
    head = V5_HEADER.pack(5, len(flows), 1_000_000, now, 0, sequence, 0, engine_id, (1 << 14 | sampling) if sampling > 1 else 0)
    recs = b"".join(
        V5_RECORD.pack(
            ipaddress.IPv4Address(f.src).packed,
            ipaddress.IPv4Address(f.dst).packed,
            b"\0\0\0\0",
            0,
            0,
            f.packets,
            f.bytes,
            0,
            0,
            f.src_port,
            f.dst_port,
            0,
            f.protocol,
            0,
            0,
            0,
            0,
            0,
        )
        for f in flows
    )
    return head + recs


FLOW_FIELDS_V4 = [(IE_SRC_V4, 4), (IE_DST_V4, 4), (IE_SRC_PORT, 2), (IE_DST_PORT, 2), (IE_PROTO, 1), (IE_OCTETS, 8), (IE_PACKETS, 8)]
FLOW_FIELDS_V6 = [(IE_SRC_V6, 16), (IE_DST_V6, 16), *FLOW_FIELDS_V4[2:]]


def _record(f: Flow, fields: list[tuple[int, int]]) -> bytes:
    vals = {
        IE_SRC_V4: f.src,
        IE_DST_V4: f.dst,
        IE_SRC_V6: f.src,
        IE_DST_V6: f.dst,
        IE_SRC_PORT: f.src_port,
        IE_DST_PORT: f.dst_port,
        IE_PROTO: f.protocol,
        IE_OCTETS: f.bytes,
        IE_PACKETS: f.packets,
    }
    out = b""
    for ie, ln in fields:
        v = vals[ie]
        out += ipaddress.ip_address(v).packed if isinstance(v, str) else int(v).to_bytes(ln, "big")
    return out


def encode_ipfix(
    flows: list[Flow], *, domain: int = 1, sampling: int = 0, sequence: int = 0, version: int = 10, with_templates: bool = True
) -> bytes:
    """IPFIX (version 10) or NetFlow v9 (version 9): templates, an optional sampling options record, then data."""
    ipfix = version == 10
    t_set, o_set = (2, 3) if ipfix else (0, 1)
    sets = b""
    if with_templates:
        body = b""
        for tid, fields in ((256, FLOW_FIELDS_V4), (257, FLOW_FIELDS_V6)):
            body += struct.pack("!HH", tid, len(fields)) + b"".join(struct.pack("!HH", ie, ln) for ie, ln in fields)
        sets += struct.pack("!HH", t_set, 4 + len(body)) + body
        if sampling:
            # Scope: observation domain (IPFIX IE 149) / system (v9 scope type 1); option: samplingInterval.
            if ipfix:
                body = struct.pack("!HHH", 258, 2, 1) + struct.pack("!HHHH", 149, 4, IE_SAMPLING_INTERVAL, 4)
            else:
                body = struct.pack("!HHH", 258, 4, 4) + struct.pack("!HHHH", 1, 4, IE_SAMPLING_INTERVAL, 4)
            sets += struct.pack("!HH", o_set, 4 + len(body)) + body
    if sampling and with_templates:
        body = struct.pack("!II", domain, sampling)
        sets += struct.pack("!HH", 258, 4 + len(body)) + body
    for tid, fields, group in (
        (256, FLOW_FIELDS_V4, [f for f in flows if ":" not in f.src]),
        (257, FLOW_FIELDS_V6, [f for f in flows if ":" in f.src]),
    ):
        if group:
            body = b"".join(_record(f, fields) for f in group)
            sets += struct.pack("!HH", tid, 4 + len(body)) + body
    now = int(datetime.now(UTC).timestamp())
    if ipfix:
        return IPFIX_HEADER.pack(10, IPFIX_HEADER.size + len(sets), now, sequence, domain) + sets
    return V9_HEADER.pack(9, len(flows), 1_000_000, now, sequence, domain) + sets


# --- Collector: UDP receiver, per-minute rollup buffer, batched writes. ---

# (bucket, exporter, version, ip version, protocol, src, dst, service port, sampling)
RollKey = tuple[Any, ...]


class Collector:
    """Buffers decoded flows as per-minute sums and writes them in batches; drops when the buffer is full."""

    def __init__(self, state: AppState, max_keys: int = 100_000) -> None:
        self.state = state
        self.decoder = Decoder(state.settings.flow_sampling_rate)
        self.allowed = [ipaddress.ip_network(c, strict=False) for c in state.settings.flow_allowed_exporters]
        self.buffer: dict[RollKey, list[int]] = {}
        self.max_keys = max_keys
        self.datagrams = 0
        self.flows = 0
        self.rejected = 0
        self.malformed = 0
        self.dropped = 0
        self.stored = 0
        self.last_at: datetime | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._flusher: asyncio.Task[None] | None = None
        self.listening: str | None = None
        self.error: str | None = None

    def submit(self, data: bytes, address: str, at: datetime | None = None) -> int:
        self.datagrams += 1
        self.last_at = utcnow()
        if self.allowed and not any(ipaddress.ip_address(address) in n for n in self.allowed if n.version == (6 if ":" in address else 4)):
            self.rejected += 1
            return 0
        try:
            flows = self.decoder.decode(data, address)
        except (FlowDecodeError, struct.error, ValueError, IndexError):
            self.malformed += 1
            return 0
        version = struct.unpack_from("!H", data)[0]
        bucket = (at or utcnow()).replace(second=0, microsecond=0)
        for f in flows:
            key: RollKey = (bucket, address, version, f.ip_version, f.protocol, f.src, f.dst, f.port, f.sampling)
            acc = self.buffer.get(key)
            if acc is None:
                if len(self.buffer) >= self.max_keys:
                    self.dropped += 1
                    continue
                acc = self.buffer[key] = [0, 0, 0]
            acc[0] += f.bytes
            acc[1] += f.packets
            acc[2] += 1
        self.flows += len(flows)
        return len(flows)

    async def flush(self, *, demo: bool = False) -> int:
        if not self.buffer:
            return 0
        batch, self.buffer = self.buffer, {}
        async with self.state.db.sessionmaker() as db:
            if not await self.state.modules.is_enabled(db, "flows"):
                self.dropped += len(batch)
                return 0
            ips = {k[5] for k in batch} | {k[6] for k in batch}
            devices = {
                d.ip_address: d.id for d in await db.scalars(select(Device).where(Device.ip_address.in_(ips), Device.present.is_(True)))
            }
            clients = {
                c.ip_address: c.id for c in await db.scalars(select(Client).where(Client.ip_address.in_(ips), Client.present.is_(True)))
            }
            db.add_all(
                FlowRollup(
                    bucket=bucket,
                    exporter_ip=exporter,
                    version=version,
                    ip_version=ipv,
                    protocol=proto,
                    src_ip=src,
                    dst_ip=dst,
                    port=port,
                    bytes=b,
                    packets=p,
                    flows=n,
                    sampling=sampling,
                    src_client_id=clients.get(src),
                    dst_client_id=clients.get(dst),
                    src_device_id=devices.get(src),
                    dst_device_id=devices.get(dst),
                    is_demo=demo,
                )
                for (bucket, exporter, version, ipv, proto, src, dst, port, sampling), (b, p, n) in batch.items()
            )
            await db.commit()
        self.stored += len(batch)
        return len(batch)

    async def start(self, bind: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        collector = self

        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                collector.submit(data, addr[0])

        try:
            self._transport, _ = await loop.create_datagram_endpoint(Protocol, local_addr=(bind, port))
            sock = self._transport.get_extra_info("sockname")
            self.listening = f"udp://{sock[0]}:{sock[1]}"
        except OSError as e:
            self.error = f"Cannot listen on UDP {bind}:{port}: {e.strerror or e}"
            log.warning(self.error)
            return
        self._flusher = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 - keep receiving even if one batch fails to store
                log.exception("flow flush failed")

    async def stop(self) -> None:
        if self._flusher:
            self._flusher.cancel()
        if self._transport:
            self._transport.close()
        with contextlib.suppress(Exception):
            await self.flush()


async def retention_job(ctx: JobContext) -> dict[str, Any]:
    s = ctx.state.settings
    cutoff = utcnow() - timedelta(days=s.flow_retention_days)
    old = (await ctx.db.execute(delete(FlowRollup).where(FlowRollup.bucket < cutoff))).rowcount or 0
    total = await ctx.db.scalar(select(func.count()).select_from(FlowRollup)) or 0
    trimmed = 0
    if total > s.flow_max_rows:
        edge = await ctx.db.scalar(select(FlowRollup.bucket).order_by(FlowRollup.bucket.desc()).offset(s.flow_max_rows).limit(1))
        if edge:
            trimmed = (await ctx.db.execute(delete(FlowRollup).where(FlowRollup.bucket <= edge))).rowcount or 0
    return {"expired": old, "trimmed": trimmed}


# Demo conversations between simulator clients and documentation-range internet addresses:
# (src, dst, protocol, src port, dst port, bytes per minute at peak, packets per minute at peak).
DEMO_FLOWS = [
    ("10.0.20.105", "10.0.30.101", 6, 52114, 445, 180_000_000, 140_000),  # workstation -> unraid-tower SMB
    ("10.0.30.101", "10.0.20.105", 6, 445, 52114, 12_000_000, 90_000),
    ("10.0.30.101", "198.51.100.40", 6, 40122, 443, 95_000_000, 70_000),  # unraid-tower offsite backup
    ("10.0.40.117", "203.0.113.80", 6, 50311, 443, 2_400_000, 3_000),  # tv-living streaming (download direction below)
    ("203.0.113.80", "10.0.40.117", 6, 443, 50311, 160_000_000, 115_000),
    ("10.0.40.118", "10.0.30.111", 6, 48810, 8123, 300_000, 900),  # thermostat -> home-assistant
    ("10.0.20.114", "1.1.1.1", 17, 53001, 53, 90_000, 700),  # phone-a DNS
    ("10.0.20.114", "203.0.113.25", 17, 61000, 443, 14_000_000, 12_000),  # phone-a QUIC
    ("10.0.30.112", "198.51.100.7", 6, 39012, 443, 6_000_000, 5_200),  # docker-host image pulls
    ("10.0.40.119", "203.0.113.200", 6, 49152, 8883, 40_000, 300),  # smart-plug MQTT to cloud
    ("2001:db8:20::113", "2001:db8:ffff::443", 6, 51000, 443, 22_000_000, 18_000),  # laptop-work over IPv6
    ("10.0.60.123", "203.0.113.90", 6, 50500, 443, 3_500_000, 3_100),  # guest-phone
]


async def seed_demo(state: AppState, hours: int = 24) -> int:
    """Encode labelled synthetic IPFIX exports, decode them with the real decoder and store them over the last day.

    A collector of its own keeps demo exports out of the live receiver's counters and exporter list.
    """
    collector = Collector(state)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    seq = 0
    for i in range(hours * 6):
        t = now - timedelta(minutes=10 * i)
        # Evening peak, quiet early morning; backups run in a burst at 03:00.
        hour = t.hour
        load = 0.15 + 0.85 * max(0.0, 1 - abs(hour - 20) / 8)
        flows = []
        for src, dst, proto, sp, dp, b, p in DEMO_FLOWS:
            k = load * 10
            if dst == "198.51.100.40":
                k = 10.0 if hour == 3 else 0.02
            if k > 0.01:
                flows.append(Flow(src, dst, proto, sp, dp, int(b * k / 512), max(1, int(p * k / 512))))
        collector.submit(encode_ipfix(flows, domain=1, sampling=512, sequence=seq), "10.0.1.1", at=t)
        seq += len(flows)
    return await collector.flush(demo=True)

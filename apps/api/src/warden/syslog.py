"""Syslog receiver: parse, categorise, match to inventory and store in batches.

Formats: RFC 5424, RFC 3164 (BSD) and a CEF payload inside either (the
standard ArcSight Common Event Format header and key=value extension). The
category is a keyword heuristic over the message and CEF name and is labelled
as such; Fabric Warden does not claim to understand every vendor's log schema.
UniFi's own CEF field set is not mapped specially because it has not been
verified against a published reference.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from warden.db import utcnow
from warden.models import Client, Device, LogEvent

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

log = logging.getLogger(__name__)

PRI = re.compile(r"^<(\d{1,3})>")
R5424 = re.compile(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[.*?\])+) ?(.*)$", re.S)
R3164 = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) (\S+) ([^:\[\s]+)(?:\[\d+\])?: ?(.*)$", re.S)
CEF = re.compile(r"CEF:(\d+)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)$", re.S)
CEF_KV = re.compile(r"(\w+)=((?:[^=\\]|\\.)*?)(?=\s+\w+=|$)")
SD = re.compile(r'\[([^\s\]]+)((?:\s+[^=\s]+="(?:[^"\\]|\\.)*")*)\]')
SD_KV = re.compile(r'([^=\s]+)="((?:[^"\\]|\\.)*)"')

CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ids", re.compile(r"\b(ids|ips|intrusion|threat|signature|suricata|snort|malware|exploit)\b", re.I)),
    ("firewall", re.compile(r"\b(firewall|iptables|nftables|blocked|dropped|deny|denied|reject|drop|accept)\b|\bDPT=|\bSRC=", re.I)),
    ("auth", re.compile(r"\b(login|logon|logout|auth\w*|password|ssh|sudo|radius|802\.1x|failed for)\b", re.I)),
    ("system", re.compile(r"\b(kernel|systemd|reboot|restart|upgrade|firmware|daemon|started|stopped|link (up|down))\b", re.I)),
]


@dataclass
class Parsed:
    source_ip: str
    message: str
    severity: int = 6
    facility: int | None = None
    host: str = ""
    app: str = ""
    logged_at: datetime | None = None
    fmt: str = "raw"
    fields: dict[str, Any] = field(default_factory=dict)
    category: str = "other"


def _cef(text: str, p: Parsed) -> None:
    m = CEF.search(text)
    if not m:
        return
    ext = {k: v.replace("\\=", "=").replace("\\\\", "\\").strip() for k, v in CEF_KV.findall(m.group(8))}
    p.fmt = "cef"
    p.fields = {
        "cef_version": m.group(1),
        "vendor": m.group(2),
        "product": m.group(3),
        "product_version": m.group(4),
        "signature_id": m.group(5),
        "name": m.group(6),
        "cef_severity": m.group(7),
        **ext,
    }
    p.message = m.group(6) + (f" · {ext['msg']}" if ext.get("msg") else "")
    try:
        # CEF severity 0–10 (10 most severe) onto syslog 7–0.
        p.severity = max(0, min(7, 7 - round(int(m.group(7)) * 0.7)))
    except ValueError:
        pass


def parse(raw: bytes | str, source_ip: str) -> Parsed:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = text.strip().lstrip("\ufeff")
    p = Parsed(source_ip=source_ip, message=text)
    m = PRI.match(text)
    if m:
        pri = int(m.group(1))
        p.facility, p.severity = pri // 8, pri % 8
        text = text[m.end() :]
    if m := R5424.match(text):
        ts, host, app = m.group(1), m.group(2), m.group(3)
        p.fmt, p.host, p.app = "5424", "" if host == "-" else host, "" if app == "-" else app
        with contextlib.suppress(ValueError):
            p.logged_at = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts != "-" else None
        if m.group(6) != "-":
            for sid, params in SD.findall(m.group(6)):
                p.fields[sid] = dict(SD_KV.findall(params))
        p.message = m.group(7).lstrip("\ufeff")
    elif m := R3164.match(text):
        p.fmt, p.host, p.app, p.message = "3164", m.group(2), m.group(3), m.group(4)
        with contextlib.suppress(ValueError):
            now = datetime.now(UTC)
            ts = datetime.strptime(f"{now.year} {m.group(1)}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
            p.logged_at = ts if ts <= now + timedelta(days=1) else ts.replace(year=now.year - 1)
    else:
        p.message = text
    _cef(p.message if "CEF:" in p.message else text, p)
    haystack = f"{p.app} {p.message} {p.fields.get('name', '')}"
    p.category = next((c for c, rx in CATEGORY_RULES if rx.search(haystack)), "other")
    return p


class Ingest:
    """Buffers parsed messages and writes them in batches; drops when the buffer is full rather than blocking the socket."""

    def __init__(self, state: AppState, max_buffer: int = 20_000) -> None:
        self.state = state
        self.buffer: list[Parsed] = []
        self.max_buffer = max_buffer
        self.received = 0
        self.dropped = 0
        self.stored = 0
        self.last_at: datetime | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._flusher: asyncio.Task[None] | None = None
        self.listening: str | None = None
        self.error: str | None = None

    def submit(self, raw: bytes | str, source_ip: str) -> None:
        self.received += 1
        self.last_at = utcnow()
        if len(self.buffer) >= self.max_buffer:
            self.dropped += 1
            return
        try:
            self.buffer.append(parse(raw, source_ip))
        except Exception:  # noqa: BLE001 - one malformed datagram must never stop the receiver
            self.dropped += 1

    async def flush(self, *, demo: bool = False) -> int:
        if not self.buffer:
            return 0
        batch, self.buffer = self.buffer, []
        async with self.state.db.sessionmaker() as db:
            if not await self.state.modules.is_enabled(db, "logs"):
                self.dropped += len(batch)
                return 0
            ips = {p.source_ip for p in batch}
            devices = {
                d.ip_address: d.id for d in await db.scalars(select(Device).where(Device.ip_address.in_(ips), Device.present.is_(True)))
            }
            clients = {
                c.ip_address: c.id for c in await db.scalars(select(Client).where(Client.ip_address.in_(ips), Client.present.is_(True)))
            }
            db.add_all(
                LogEvent(
                    received_at=utcnow(),
                    logged_at=p.logged_at,
                    source_ip=p.source_ip,
                    host=p.host[:255],
                    app=p.app[:128],
                    facility=p.facility,
                    severity=p.severity,
                    category=p.category,
                    message=p.message[:8000],
                    fmt=p.fmt,
                    fields=p.fields,
                    device_id=devices.get(p.source_ip),
                    client_id=clients.get(p.source_ip),
                    is_demo=demo,
                )
                for p in batch
            )
            await db.commit()
        self.stored += len(batch)
        return len(batch)

    async def start(self, bind: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        ingest = self

        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                ingest.submit(data, addr[0])

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
            await asyncio.sleep(2)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 - keep receiving even if one batch fails to store
                log.exception("syslog flush failed")

    async def stop(self) -> None:
        if self._flusher:
            self._flusher.cancel()
        if self._transport:
            self._transport.close()
        with contextlib.suppress(Exception):
            await self.flush()


async def retention_job(ctx: JobContext) -> dict[str, Any]:
    s = ctx.state.settings
    cutoff = utcnow() - timedelta(days=s.log_retention_days)
    old = (await ctx.db.execute(delete(LogEvent).where(LogEvent.received_at < cutoff))).rowcount or 0
    total = await ctx.db.scalar(select(func.count()).select_from(LogEvent)) or 0
    trimmed = 0
    if total > s.log_max_rows:
        edge = await ctx.db.scalar(select(LogEvent.received_at).order_by(LogEvent.received_at.desc()).offset(s.log_max_rows).limit(1))
        if edge:
            trimmed = (await ctx.db.execute(delete(LogEvent).where(LogEvent.received_at <= edge))).rowcount or 0
    return {"expired": old, "trimmed": trimmed}


DEMO_LINES = [
    (
        "10.0.1.1",
        '<36>1 {ts} Gateway kernel - - - [WAN_LOCAL-default-D] DESCR="Block inbound" IN=eth4 '
        "SRC=185.220.101.4 DST=203.0.113.10 PROTO=TCP SPT=51544 DPT=22",
    ),
    (
        "10.0.1.1",
        '<36>1 {ts} Gateway kernel - - - [LAN_IN-2002-D] DESCR="Block IoT to Internal" '
        "IN=br40 SRC=10.0.40.112 DST=10.0.20.105 PROTO=TCP DPT=445",
    ),
    (
        "10.0.1.1",
        "<34>1 {ts} Gateway suricata - - - CEF:0|Demo|Gateway IDS|1.0|2027865|ET SCAN Suspicious inbound to SSH|8|"
        "src=185.220.101.4 dst=203.0.113.10 dpt=22 act=blocked",
    ),
    ("10.0.1.1", "<38>1 {ts} Gateway sshd - - - Failed password for admin from 198.51.100.23 port 40122 ssh2"),
    ("10.0.30.101", "<30>{bsd} unraid-tower emhttpd: spinning down /dev/sdd"),
    ("10.0.30.101", "<27>{bsd} unraid-tower kernel: ata4: hard resetting link"),
    ("10.0.30.102", "<30>{bsd} proxmox-01 pvedaemon[1123]: <root@pam> starting task UPID:proxmox-01:vzdump"),
    ("10.0.1.3", "<30>{bsd} Rack-PoE-Switch switch: port 11 link up 1000Mbps"),
    ("10.0.40.112", "<29>{bsd} thermostat app: firmware check complete"),
]


async def seed_demo(state: AppState, ingest: Ingest, count: int = 120) -> int:
    """Feed labelled synthetic lines through the real parser, spread over the last hour."""
    now = datetime.now(UTC)
    for i in range(count):
        ip, tmpl = DEMO_LINES[i % len(DEMO_LINES)]
        t = now - timedelta(seconds=(count - i) * 30)
        ingest.submit(
            tmpl.format(
                ts=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                bsd=t.strftime("%b %d %H:%M:%S").replace(" 0", "  ", 1) if t.day < 10 else t.strftime("%b %d %H:%M:%S"),
            ),
            ip,
        )
    return await ingest.flush(demo=True)

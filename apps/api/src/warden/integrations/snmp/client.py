"""Read-only SNMP sessions and the IF-MIB interface collector.

Read-only by construction: a session can only GET and walk. There is no SET
method, and pysnmp's set command is never imported (a test enforces this).
Every object read is a standard MIB-II / IF-MIB object listed in
official_api_matrix.json next to this file, with the RFC that defines it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"  # TimeTicks, hundredths of a second
SYS_NAME = "1.3.6.1.2.1.1.5.0"

IF = "1.3.6.1.2.1.2.2.1"  # ifTable (RFC 2863)
IFX = "1.3.6.1.2.1.31.1.1.1"  # ifXTable (RFC 2863)
COLUMNS = {
    "descr": f"{IF}.2",
    "type": f"{IF}.3",
    "mac": f"{IF}.6",
    "admin": f"{IF}.7",
    "oper": f"{IF}.8",
    "in_discards": f"{IF}.13",
    "in_errors": f"{IF}.14",
    "out_discards": f"{IF}.19",
    "out_errors": f"{IF}.20",
    "name": f"{IFX}.1",
    "in_octets": f"{IFX}.6",  # ifHCInOctets, 64-bit
    "out_octets": f"{IFX}.10",  # ifHCOutOctets, 64-bit
    "high_speed": f"{IFX}.15",  # Mbit/s
    "alias": f"{IFX}.18",
}
COUNTERS = ("in_octets", "out_octets", "in_errors", "out_errors", "in_discards", "out_discards")
OPER_STATUS = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}
# ethernetCsmacd, gigabitEthernet, fastEther, fastEtherFX: the types switch and gateway ports report.
PHYSICAL_TYPES = {6, 62, 69, 117}

AUTH_PROTOCOLS = ("none", "MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512")
PRIV_PROTOCOLS = ("none", "DES", "AES", "AES-192", "AES-256")


class SnmpError(Exception):
    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind  # auth | unreachable | protocol | error


@dataclass(frozen=True)
class Credentials:
    version: str  # v3 | v2c
    username: str = ""
    auth_protocol: str = "SHA"
    auth_key: str = ""
    priv_protocol: str = "AES"
    priv_key: str = ""
    community: str = ""


class Session(Protocol):
    """What a collector needs from an SNMP agent: GET and walk, nothing that writes."""

    async def get(self, oids: list[str]) -> dict[str, Any]: ...

    async def walk(self, oid: str) -> dict[str, Any]: ...

    def now(self) -> float: ...


class PysnmpSession:
    """A real agent reached over UDP with pysnmp."""

    def __init__(self, host: str, port: int, creds: Credentials, *, timeout: float = 3.0, retries: int = 1) -> None:
        self.host, self.port, self.creds = host, port, creds
        self.timeout, self.retries = timeout, retries
        self._engine: Any = None
        self._target: Any = None

    def now(self) -> float:
        return time.time()

    def _auth(self) -> Any:
        from pysnmp.hlapi.v3arch import asyncio as hl

        c = self.creds
        if c.version == "v2c":
            return hl.CommunityData(c.community, mpModel=1)  # noqa: S508 - v2c offered for agents without v3; the UI recommends v3
        auth = {
            "none": hl.usmNoAuthProtocol,
            "MD5": hl.usmHMACMD5AuthProtocol,
            "SHA": hl.usmHMACSHAAuthProtocol,
            "SHA-224": hl.usmHMAC128SHA224AuthProtocol,
            "SHA-256": hl.usmHMAC192SHA256AuthProtocol,
            "SHA-384": hl.usmHMAC256SHA384AuthProtocol,
            "SHA-512": hl.usmHMAC384SHA512AuthProtocol,
        }[c.auth_protocol]
        priv = {
            "none": hl.usmNoPrivProtocol,
            "DES": hl.usmDESPrivProtocol,
            "AES": hl.usmAesCfb128Protocol,
            "AES-192": hl.usmAesCfb192Protocol,
            "AES-256": hl.usmAesCfb256Protocol,
        }[c.priv_protocol]
        return hl.UsmUserData(
            c.username,
            authKey=c.auth_key or None,
            privKey=c.priv_key or None,
            authProtocol=auth,
            privProtocol=priv,
        )

    async def _setup(self) -> tuple[Any, Any]:
        from pysnmp.hlapi.v3arch import asyncio as hl

        if self._engine is None:
            self._engine = hl.SnmpEngine()
            try:
                self._target = await hl.UdpTransportTarget.create((self.host, self.port), timeout=self.timeout, retries=self.retries)
            except Exception as e:  # name resolution failures surface here
                raise SnmpError(f"Cannot reach {self.host}: {e}", kind="unreachable") from e
        return self._engine, self._target

    @staticmethod
    def _raise(indication: Any, status: Any) -> None:
        if indication:
            text = str(indication)
            low = text.lower()
            if "timeout" in low or "no snmp response" in low:
                raise SnmpError("The agent did not answer. Check that SNMP is enabled and UDP 161 is reachable.", kind="unreachable")
            if any(
                k in low for k in ("unknown usm user", "unknownusername", "digest", "wrongdigest", "decrypt", "authentication", "privacy")
            ):
                raise SnmpError(f"The agent refused the credentials ({text})", kind="auth")
            raise SnmpError(text, kind="error")
        if status:
            raise SnmpError(f"The agent returned {status.prettyPrint()}", kind="protocol")

    @staticmethod
    def _value(v: Any) -> Any:
        name = type(v).__name__
        if name in ("NoSuchObject", "NoSuchInstance", "EndOfMibView", "Null"):
            return None
        if name in ("OctetString", "IpAddress", "Opaque", "Bits"):
            return bytes(v.asOctets())
        if name == "ObjectIdentifier":
            return str(v)
        return int(v)

    async def get(self, oids: list[str]) -> dict[str, Any]:
        from pysnmp.hlapi.v3arch import asyncio as hl

        engine, target = await self._setup()
        ind, st, _, binds = await hl.get_cmd(
            engine, self._auth(), target, hl.ContextData(), *[hl.ObjectType(hl.ObjectIdentity(o)) for o in oids], lookupMib=False
        )
        self._raise(ind, st)
        return {str(name): self._value(val) for name, val in binds}

    async def walk(self, oid: str) -> dict[str, Any]:
        from pysnmp.hlapi.v3arch import asyncio as hl

        engine, target = await self._setup()
        out: dict[str, Any] = {}
        gen = hl.bulk_walk_cmd(
            engine,
            self._auth(),
            target,
            hl.ContextData(),
            0,
            25,
            hl.ObjectType(hl.ObjectIdentity(oid)),
            lexicographicMode=False,
            lookupMib=False,
        )
        async for ind, st, _, binds in gen:
            self._raise(ind, st)
            for name, val in binds:
                v = self._value(val)
                if v is not None:
                    out[str(name)] = v
        return out

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close_dispatcher()


# -- collector --------------------------------------------------------------------------


@dataclass
class IfData:
    if_index: int
    name: str = ""
    descr: str = ""
    alias: str = ""
    if_type: int | None = None
    mac_address: str | None = None
    admin_up: bool | None = None
    oper_status: str = "unknown"
    speed_mbps: int | None = None
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def physical(self) -> bool:
        return self.if_type in PHYSICAL_TYPES


@dataclass
class Snapshot:
    sys_name: str
    sys_descr: str
    uptime_s: int | None
    read_at: float
    interfaces: list[IfData]


def _text(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").strip("\x00").strip()
    return "" if v is None else str(v)


def _mac(v: Any) -> str | None:
    if isinstance(v, bytes) and len(v) == 6 and any(v):
        return ":".join(f"{b:02x}" for b in v)
    return None


_PORT_RE = re.compile(r"\bport:?\s*(\d{1,3})\b", re.I)


def port_number(name: str, descr: str) -> int | None:
    """The front-panel port number when the interface name says it ("Port 7", "Slot: 0 Port: 7 ...").

    A heuristic: names such as eth3 or switch0 are left unmatched because their
    numbering does not reliably follow the front panel.
    """
    for text in (name, descr):
        if m := _PORT_RE.search(text or ""):
            return int(m.group(1))
    return None


async def collect(session: Session) -> Snapshot:
    system = await session.get([SYS_DESCR, SYS_UPTIME, SYS_NAME])
    read_at = session.now()
    columns = {key: await session.walk(oid) for key, oid in COLUMNS.items()}
    if not columns["descr"] and not columns["name"]:
        raise SnmpError("The agent answered but exposes no IF-MIB interfaces to this user (check the SNMP view).", kind="protocol")

    rows: dict[int, IfData] = {}
    for key, oid in COLUMNS.items():
        prefix = oid + "."
        for full, value in columns[key].items():
            if not full.startswith(prefix):
                continue
            idx = int(full[len(prefix) :].split(".")[0])
            row = rows.setdefault(idx, IfData(if_index=idx))
            if key in COUNTERS:
                row.counters[key] = int(value)
            elif key == "descr":
                row.descr = _text(value)
            elif key == "name":
                row.name = _text(value)
            elif key == "alias":
                row.alias = _text(value)
            elif key == "type":
                row.if_type = int(value)
            elif key == "mac":
                row.mac_address = _mac(value)
            elif key == "admin":
                row.admin_up = int(value) == 1
            elif key == "oper":
                row.oper_status = OPER_STATUS.get(int(value), "unknown")
            elif key == "high_speed":
                row.speed_mbps = int(value) or None
    uptime = system.get(SYS_UPTIME)
    return Snapshot(
        sys_name=_text(system.get(SYS_NAME)),
        sys_descr=_text(system.get(SYS_DESCR))[:500],
        uptime_s=int(uptime) // 100 if isinstance(uptime, int) else None,
        read_at=read_at,
        interfaces=[rows[i] for i in sorted(rows)],
    )

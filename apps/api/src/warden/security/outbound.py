"""Guards for outbound connections to user-supplied addresses (SSRF).

Fabric Warden talks to equipment on the operator's own network, so private
(RFC 1918), unique-local and loopback addresses are expected and allowed.
What is refused by default:

* schemes other than ``http``/``https`` (``unix://`` only where a caller opts in),
* credentials embedded in the URL (they would end up in logs and audit details),
* link-local addresses (169.254.0.0/16, fe80::/10) — this includes the cloud
  instance-metadata service at 169.254.169.254 and fd00:ec2::254,
* well-known metadata host names, unspecified (0.0.0.0, ::), multicast and
  reserved/broadcast ranges.

Two checks apply. ``check_url``/``check_host`` validate what a person types,
before anything is stored or contacted. ``GuardedTransport`` re-checks every
request at connection time after DNS resolution, so a name that resolves to a
blocked address (or a redirect to one) is refused too. A small window between
that resolution and the socket connect remains (DNS rebinding); it is
documented in SECURITY_MODEL.md.

Operators can opt back in: ``WARDEN_OUTBOUND_ALLOW_LINK_LOCAL=true`` allows
link-local addresses, and ``WARDEN_OUTBOUND_ALLOWED_CIDRS`` exempts specific
networks from every block. ``WARDEN_OUTBOUND_BLOCKED_CIDRS`` adds networks to
refuse (for example, the Docker bridge the API runs on).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.com",
        "metadata.platformequinix.com",
    }
)
_LINK_LOCAL = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))
_METADATA_IPS = (ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200"))


class OutboundBlocked(ValueError):
    """The destination is not allowed by the outbound policy."""


@dataclass(frozen=True)
class OutboundPolicy:
    allow_link_local: bool = False
    allowed: tuple[IPNetwork, ...] = ()
    blocked: tuple[IPNetwork, ...] = field(default_factory=tuple)

    @classmethod
    def from_settings(cls, settings: object) -> OutboundPolicy:
        def nets(values: object) -> tuple[IPNetwork, ...]:
            return tuple(ipaddress.ip_network(str(v), strict=False) for v in (values or []))  # type: ignore[attr-defined]

        return cls(
            allow_link_local=bool(getattr(settings, "outbound_allow_link_local", False)),
            allowed=nets(getattr(settings, "outbound_allowed_cidrs", ())),
            blocked=nets(getattr(settings, "outbound_blocked_cidrs", ())),
        )

    def reason(self, ip: IPAddress) -> str | None:
        """Why ``ip`` is refused, or None when it is allowed."""
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if any(ip in n for n in self.allowed):
            return None
        if any(ip in n for n in self.blocked):
            return "is in a blocked network (WARDEN_OUTBOUND_BLOCKED_CIDRS)"
        if ip in _METADATA_IPS:
            return "is a cloud metadata address"
        if ip.is_unspecified:
            return "is unspecified"
        if ip.is_multicast:
            return "is multicast"
        if any(ip in n for n in _LINK_LOCAL):
            return None if self.allow_link_local else "is link-local (set WARDEN_OUTBOUND_ALLOW_LINK_LOCAL=true to allow)"
        if isinstance(ip, ipaddress.IPv4Address) and (ip.is_reserved or ip == ipaddress.IPv4Address("255.255.255.255")):
            return "is reserved"
        return None

    def check_ip(self, ip: IPAddress, label: str) -> None:
        if why := self.reason(ip):
            raise OutboundBlocked(f"{label} {why}")


DEFAULT_POLICY = OutboundPolicy()
_active: OutboundPolicy = DEFAULT_POLICY


def set_policy(policy: OutboundPolicy) -> None:
    """Install the process-wide policy (done once in ``create_app`` from settings)."""
    global _active
    _active = policy


def active_policy() -> OutboundPolicy:
    return _active


def _literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return None


def check_host(host: str, policy: OutboundPolicy | None = None) -> str:
    """Validate a bare host name or IP address (no DNS lookup)."""
    policy = policy or _active
    h = host.strip().rstrip(".").lower()
    if not h:
        raise OutboundBlocked("enter a host name or IP address")
    if h in METADATA_HOSTS:
        raise OutboundBlocked(f"{host} is a cloud metadata host name")
    ip = _literal(h)
    if ip is not None:
        policy.check_ip(ip, host)
    return host


def check_url(url: str, policy: OutboundPolicy | None = None, *, allow_unix: bool = False) -> str:
    """Validate a user-supplied URL before storing or contacting it (no DNS lookup)."""
    policy = policy or _active
    if allow_unix and url.startswith("unix:///"):
        return url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise OutboundBlocked("use an http:// or https:// URL")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise OutboundBlocked("don't put credentials in the URL; use the key or token field")
    if not parts.hostname:
        raise OutboundBlocked("the URL needs a host")
    try:
        _ = parts.port
    except ValueError:
        raise OutboundBlocked("the port is not valid") from None
    check_host(parts.hostname, policy)
    return url


async def _resolve(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    out: list[IPAddress] = []
    for info in infos:
        ip = _literal(str(info[4][0]))
        if ip is not None:
            out.append(ip)
    return out


class GuardedTransport(httpx.AsyncBaseTransport):
    """Wraps a real transport; refuses requests whose host resolves to a blocked address."""

    def __init__(self, inner: httpx.AsyncBaseTransport, policy: OutboundPolicy | None = None) -> None:
        self._inner = inner
        self._policy = policy or _active

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            check_host(host, self._policy)
            if _literal(host) is None:
                port = request.url.port or (443 if request.url.scheme == "https" else 80)
                try:
                    addresses = await _resolve(host, port)
                except OSError as exc:
                    raise httpx.ConnectError(f"could not resolve {host}: {exc}", request=request) from exc
                for ip in addresses:
                    self._policy.check_ip(ip, f"{host} resolves to {ip}, which")
        except OutboundBlocked as exc:
            raise httpx.ConnectError(f"blocked by outbound policy: {exc}", request=request) from exc
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_transport(verify: object = True, policy: OutboundPolicy | None = None) -> GuardedTransport:
    return GuardedTransport(httpx.AsyncHTTPTransport(verify=verify), policy)  # type: ignore[arg-type]


def validate_optional_url(v: str | None, *, allow_unix: bool = False) -> str | None:
    """Pydantic helper: ``None`` passes; anything else must satisfy ``check_url``."""
    return None if v is None else check_url(v, allow_unix=allow_unix)

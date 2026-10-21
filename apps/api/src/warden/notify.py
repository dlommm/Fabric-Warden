"""Notification channels: webhook, ntfy and email.

Channel secrets (webhook URL and signing secret, ntfy topic and token, SMTP
password) live in the credential vault as one JSON document per channel; the
channel row holds only non-secret settings and a display hint.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import utcnow
from warden.models import NotificationChannel, NotificationDelivery

if TYPE_CHECKING:
    from warden.app_state import AppState

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
NTFY_PRIORITY = {"critical": "5", "warning": "4", "info": "3"}
TIMEOUT = 10.0


@dataclass
class Message:
    kind: str  # firing | resolved | test
    severity: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    url: str | None = None


def display_hint(kind: str, config: dict[str, Any], secret: dict[str, Any]) -> str:
    """What the UI shows instead of the secret: host, server and a masked topic, or recipients."""
    if kind == "webhook":
        parts = urlsplit(secret.get("url", ""))
        return f"{parts.scheme}://{parts.hostname}" if parts.hostname else ""
    if kind == "ntfy":
        topic = secret.get("topic", "")
        host = urlsplit(config.get("server", "")).hostname or ""
        return f"{host}/{topic[:3]}…" if topic else host
    if kind == "email":
        return ", ".join(config.get("to", []))
    return ""


async def _secret(state: AppState, db: AsyncSession, channel: NotificationChannel) -> dict[str, Any]:
    if not channel.credential_id:
        return {}
    return json.loads(await state.vault.reveal(db, channel.credential_id))


def _http(state: AppState) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=state.http_transport, timeout=TIMEOUT, follow_redirects=False)


async def _webhook(state: AppState, secret: dict[str, Any], msg: Message) -> None:
    body = json.dumps(
        {"kind": msg.kind, "severity": msg.severity, "title": msg.title, "body": msg.body, "url": msg.url, "alert": msg.data},
        separators=(",", ":"),
    ).encode()
    headers = {"content-type": "application/json", "user-agent": "FabricWarden"}
    if key := secret.get("signing_secret"):
        headers["x-warden-signature"] = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    async with _http(state) as http:
        r = await http.post(secret["url"], content=body, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"webhook returned HTTP {r.status_code}")


async def _ntfy(state: AppState, config: dict[str, Any], secret: dict[str, Any], msg: Message) -> None:
    headers = {
        "Title": msg.title.encode("utf-8").decode("latin-1", "ignore"),
        "Priority": NTFY_PRIORITY.get(msg.severity, "3"),
        "Tags": "white_check_mark" if msg.kind == "resolved" else ("rotating_light" if msg.severity == "critical" else "warning"),
    }
    if msg.url:
        headers["Click"] = msg.url
    if token := secret.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    async with _http(state) as http:
        r = await http.post(f"{config['server'].rstrip('/')}/{secret['topic']}", content=msg.body.encode(), headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"ntfy returned HTTP {r.status_code}")


def _smtp_send(config: dict[str, Any], password: str, em: EmailMessage) -> None:
    host, port, security = config["host"], int(config.get("port", 587)), config.get("security", "starttls")
    ctx = ssl.create_default_context()
    if security == "tls":
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    with server:
        if security == "starttls":
            server.starttls(context=ctx)
        if config.get("username"):
            server.login(config["username"], password)
        server.send_message(em)


async def _email(state: AppState, config: dict[str, Any], secret: dict[str, Any], msg: Message) -> None:
    em = EmailMessage()
    em["Subject"] = f"[{msg.severity.upper()}] {msg.title}" if msg.kind == "firing" else msg.title
    em["From"] = config["from_addr"]
    em["To"] = ", ".join(config["to"])
    em.set_content(msg.body + (f"\n\n{msg.url}" if msg.url else ""))
    if state.smtp_send is not None:
        await state.smtp_send(config, secret.get("password", ""), em)
    else:
        await asyncio.to_thread(_smtp_send, config, secret.get("password", ""), em)


async def deliver(
    state: AppState, db: AsyncSession, channel: NotificationChannel, msg: Message, alert_id: str | None = None
) -> NotificationDelivery:
    """Send one message; never raises. The outcome is recorded on the channel and as a delivery."""
    error: str | None = None
    try:
        secret = await _secret(state, db, channel)
        if channel.kind == "webhook":
            await _webhook(state, secret, msg)
        elif channel.kind == "ntfy":
            await _ntfy(state, channel.config, secret, msg)
        elif channel.kind == "email":
            await _email(state, channel.config, secret, msg)
        else:
            raise RuntimeError(f"unknown channel kind {channel.kind}")
    except Exception as exc:  # delivery failures are data, not crashes
        error = f"{type(exc).__name__}: {exc}"[:500]
    channel.last_status = "failed" if error else "ok"
    channel.last_error = error
    channel.last_sent_at = utcnow()
    d = NotificationDelivery(channel_id=channel.id, alert_id=alert_id, kind=msg.kind, status="failed" if error else "sent", error=error)
    db.add(d)
    await db.flush()
    return d


async def route(state: AppState, db: AsyncSession, channel_ids: list[str], msg: Message, alert_id: str | None) -> int:
    """Deliver to the given channels (or every enabled channel) at or above their minimum severity."""
    q = select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
    if channel_ids:
        q = q.where(NotificationChannel.id.in_(channel_ids))
    sent = 0
    for ch in await db.scalars(q):
        if SEVERITY_RANK.get(msg.severity, 0) < SEVERITY_RANK.get(ch.min_severity, 0):
            continue
        await deliver(state, db, ch, msg, alert_id)
        sent += 1
    return sent

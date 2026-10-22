"""Notification channels. Secrets go to the vault and are never returned."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from sqlalchemy import select

from warden import audit, notify
from warden.api.deps import DB, Allowed, State, require_module
from warden.models import Credential, NotificationChannel, NotificationDelivery
from warden.security.outbound import check_host, check_url
from warden.security.rbac import P

router = APIRouter(prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(require_module("notifications"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.NOTIFICATIONS_MANAGE)
Severity = Literal["critical", "warning", "info"]
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
Secret = Annotated[str, StringConstraints(max_length=2048)]


def _http_url(v: str, field: str) -> str:
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{field}: use an http:// or https:// URL")
    try:
        check_url(v)
    except ValueError as exc:
        raise ValueError(f"{field}: {exc}") from None
    return v


class WebhookIn(BaseModel):
    url: Secret
    signing_secret: Secret | None = None


class NtfyIn(BaseModel):
    server: str = "https://ntfy.sh"
    topic: str
    token: Secret | None = None


class EmailIn(BaseModel):
    host: str
    port: int = Field(587, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field("", max_length=256)
    password: Secret | None = None
    from_addr: EmailStr
    to: list[EmailStr] = Field(min_length=1, max_length=20)


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["webhook", "ntfy", "email"]
    enabled: bool = True
    min_severity: Severity = "warning"
    webhook: WebhookIn | None = None
    ntfy: NtfyIn | None = None
    email: EmailIn | None = None

    @model_validator(mode="after")
    def settings_for_kind(self) -> ChannelIn:
        cfg = getattr(self, self.kind)
        if cfg is None:
            raise ValueError(f"{self.kind}: settings are required")
        _validate(self.kind, cfg)
        return self


class ChannelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    enabled: bool | None = None
    min_severity: Severity | None = None
    webhook: WebhookIn | None = None
    ntfy: NtfyIn | None = None
    email: EmailIn | None = None


def _validate(kind: str, cfg: Any) -> None:
    if kind == "webhook":
        _http_url(cfg.url, "url")
    elif kind == "ntfy":
        _http_url(cfg.server, "server")
        if not TOPIC_RE.match(cfg.topic):
            raise ValueError("topic: letters, digits, - and _ only (up to 64)")
    elif kind == "email":
        if not HOST_RE.match(cfg.host):
            try:
                ipaddress.ip_address(cfg.host)
            except ValueError:
                raise ValueError("host: enter a hostname or IP address") from None
        try:
            check_host(cfg.host)
        except ValueError as exc:
            raise ValueError(f"host: {exc}") from None


def _split(kind: str, cfg: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(non-secret config, secret document)."""
    if kind == "webhook":
        return {}, {"url": cfg.url, "signing_secret": cfg.signing_secret}
    if kind == "ntfy":
        return {"server": cfg.server.rstrip("/")}, {"topic": cfg.topic, "token": cfg.token}
    return (
        {"host": cfg.host, "port": cfg.port, "security": cfg.security, "username": cfg.username, "from_addr": cfg.from_addr, "to": cfg.to},
        {"password": cfg.password},
    )


def _channel(ch: NotificationChannel) -> dict[str, Any]:
    safe = {k: v for k, v in ch.config.items() if k != "display"}
    return {
        "id": ch.id,
        "name": ch.name,
        "kind": ch.kind,
        "enabled": ch.enabled,
        "min_severity": ch.min_severity,
        "display": ch.config.get("display", ""),
        "config": safe,
        "has_secret": bool(ch.config.get("has_signing_secret") or ch.config.get("has_token") or ch.config.get("has_password")),
        "last_status": ch.last_status,
        "last_error": ch.last_error,
        "last_sent_at": ch.last_sent_at,
        "updated_at": ch.updated_at,
    }


def _flags(kind: str, secret: dict[str, Any]) -> dict[str, bool]:
    key = {"webhook": "signing_secret", "ntfy": "token", "email": "password"}[kind]
    return {f"has_{key}": bool(secret.get(key))}


@router.get("/channels")
async def list_channels(db: DB, principal: Reader) -> dict:
    rows = await db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))
    return {"items": [_channel(c) for c in rows]}


async def _get(db: Any, channel_id: str) -> NotificationChannel:
    ch = await db.get(NotificationChannel, channel_id)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
    return ch


@router.post("/channels", status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelIn, state: State, db: DB, principal: Manager) -> dict:
    config, secret = _split(body.kind, getattr(body, body.kind))
    cred = await state.vault.store(db, kind=f"notify.{body.kind}", name=body.name, secret=json.dumps(secret))
    ch = NotificationChannel(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        min_severity=body.min_severity,
        credential_id=cred.id,
        config={**config, **_flags(body.kind, secret), "display": notify.display_hint(body.kind, config, secret)},
    )
    db.add(ch)
    await db.flush()
    await audit.record(
        db, principal.actor, "notifications.channel.create", target_type="notification_channel", target_id=ch.id, detail={"kind": ch.kind}
    )
    return _channel(ch)


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, body: ChannelPatch, state: State, db: DB, principal: Manager) -> dict:
    ch = await _get(db, channel_id)
    changed: list[str] = []
    for k in ("name", "enabled", "min_severity"):
        v = getattr(body, k)
        if v is not None:
            setattr(ch, k, v)
            changed.append(k)
    cfg = getattr(body, ch.kind)
    if cfg is not None:
        try:
            _validate(ch.kind, cfg)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        config, secret = _split(ch.kind, cfg)
        old = json.loads(await state.vault.reveal(db, ch.credential_id)) if ch.credential_id else {}
        # A blank secret field keeps the stored value, so the form never has to echo secrets.
        for k, v in secret.items():
            if v in (None, "") and old.get(k):
                secret[k] = old[k]
        cred = await db.get(Credential, ch.credential_id) if ch.credential_id else None
        if cred is not None:
            await state.vault.rotate(db, cred, json.dumps(secret))
        else:
            cred = await state.vault.store(db, kind=f"notify.{ch.kind}", name=ch.name, secret=json.dumps(secret))
            ch.credential_id = cred.id
        ch.config = {**config, **_flags(ch.kind, secret), "display": notify.display_hint(ch.kind, config, secret)}
        changed.append("settings")
    await db.flush()
    await audit.record(
        db, principal.actor, "notifications.channel.update", target_type="notification_channel", target_id=ch.id, detail={"fields": changed}
    )
    return _channel(ch)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: str, db: DB, principal: Manager) -> None:
    ch = await _get(db, channel_id)
    cred = await db.get(Credential, ch.credential_id) if ch.credential_id else None
    await db.delete(ch)
    if cred is not None:
        await db.delete(cred)
    await audit.record(
        db,
        principal.actor,
        "notifications.channel.delete",
        target_type="notification_channel",
        target_id=channel_id,
        detail={"name": ch.name},
    )


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str, state: State, db: DB, principal: Manager) -> dict:
    ch = await _get(db, channel_id)
    msg = notify.Message(
        kind="test",
        severity="info",
        title="Fabric Warden test notification",
        body=f"This is a test from Fabric Warden, sent by {principal.user.username} to the channel “{ch.name}”.",
        url=state.settings.public_url,
    )
    d = await notify.deliver(state, db, ch, msg)
    await audit.record(
        db,
        principal.actor,
        "notifications.channel.test",
        target_type="notification_channel",
        target_id=ch.id,
        outcome="success" if d.status == "sent" else "failure",
        detail={"error": d.error} if d.error else {},
    )
    return {"status": d.status, "error": d.error, "channel": _channel(ch)}


@router.get("/deliveries")
async def deliveries(db: DB, principal: Reader, channel_id: str | None = None, limit: int = 50) -> dict:
    q = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(min(max(limit, 1), 200))
    if channel_id:
        q = q.where(NotificationDelivery.channel_id == channel_id)
    return {
        "items": [
            {
                "id": d.id,
                "channel_id": d.channel_id,
                "alert_id": d.alert_id,
                "kind": d.kind,
                "status": d.status,
                "error": d.error,
                "created_at": d.created_at,
            }
            for d in await db.scalars(q)
        ]
    }

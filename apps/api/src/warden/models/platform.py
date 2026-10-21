"""Platform core tables: identity, access, vault, modules, audit, events and jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret_ref: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    roles: Mapped[list[RoleAssignment]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class RoleAssignment(IdMixin, Base):
    """Grants a built-in role to a user, optionally scoped to a single site."""

    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "role", "site_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    user: Mapped[User] = relationship(back_populates="roles")


class AuthSession(IdMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # A password-verified session awaiting a TOTP code is not yet usable.
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ApiToken(IdMixin, Base):
    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Tokens can be narrower than their owner, never broader.
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Credential(IdMixin, TimestampMixin, Base):
    """An encrypted secret. Plaintext never leaves the vault service."""

    __tablename__ = "credentials"

    kind: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    ciphertext: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(16))
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ModuleState(Base):
    __tablename__ = "module_states"

    module_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    installed_version: Mapped[str] = mapped_column(String(32))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_health: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditRecord(Base):
    """Append-only, hash-chained audit trail.

    Each record's hash covers its content plus the previous record's hash, so
    edits or deletions in the middle of the chain are detectable by verify_chain().
    """

    __tablename__ = "audit_records"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user | token | system | agent
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(96), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # success | denied | failure
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class EventRecord(Base):
    """Persisted copy of typed bus events, used for device history and timelines."""

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    type: Mapped[str] = mapped_column(String(96), index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    source: Mapped[str] = mapped_column(String(96))
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class JobRun(IdMixin, Base):
    __tablename__ = "job_runs"

    job_id: Mapped[str] = mapped_column(String(96), index=True)
    module_id: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual | event
    status: Mapped[str] = mapped_column(String(16))  # running | succeeded | failed | skipped
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)

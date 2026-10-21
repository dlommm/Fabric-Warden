"""Login, logout, MFA (TOTP) and personal API tokens."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Annotated, Literal

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from warden import audit
from warden.api.deps import DB, CurrentPrincipal, Principal, State, authenticate, client_ip, require
from warden.audit import Actor
from warden.db import utcnow
from warden.models import ApiToken, AuthSession, Credential, Setting, User
from warden.security.crypto import (
    burn_password_check,
    hash_password,
    new_token,
    token_digest,
    validate_password_strength,
    verify_password,
)
from warden.security.rbac import ROLES, P

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60


class _Throttle:
    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self._fails[key]
        cutoff = time.monotonic() - FAILURE_WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= MAX_FAILURES

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


throttle = _Throttle()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class MfaIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MeOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    roles: list[str]
    permissions: list[str]
    mfa_enabled: bool
    via: str


def me_out(p: Principal) -> MeOut:
    return MeOut(
        id=p.user.id,
        username=p.user.username,
        display_name=p.user.display_name,
        email=p.user.email,
        roles=sorted({r.role for r in p.user.roles}),
        permissions=sorted(x.value for x in p.permissions),
        mfa_enabled=p.user.mfa_enabled,
        via=p.via,
    )


async def start_session(db: DB, state: State, request: Request, response: Response, user: User, *, mfa_pending: bool) -> None:
    raw = new_token()
    ttl = timedelta(minutes=10) if mfa_pending else timedelta(hours=state.settings.session_ttl_hours)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_digest(raw),
            expires_at=utcnow() + ttl,
            mfa_pending=mfa_pending,
            user_agent=request.headers.get("user-agent", "")[:255],
            ip_address=client_ip(request) or "",
        )
    )
    response.set_cookie(
        state.settings.session_cookie_name,
        raw,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: DB, state: State) -> dict:
    key = f"{client_ip(request)}|{body.username.lower()}"
    actor = Actor("user", None, body.username, client_ip(request))
    if throttle.blocked(key):
        await audit.record(db, actor, "auth.login", outcome="denied", detail={"reason": "throttled"})
        await db.commit()  # keep the denial even though the request fails
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")
    user = await db.scalar(select(User).where(User.username == body.username))
    if user is None:
        burn_password_check(body.password)
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        throttle.fail(key)
        await audit.record(db, actor, "auth.login", outcome="denied", detail={"reason": "bad_credentials"})
        await db.commit()  # keep the denial even though the request fails
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password.")
    throttle.reset(key)
    await start_session(db, state, request, response, user, mfa_pending=user.mfa_enabled)
    if user.mfa_enabled:
        return {"mfa_required": True}
    user.last_login_at = utcnow()
    await audit.record(db, Actor("user", user.id, user.username, client_ip(request)), "auth.login")
    return {"mfa_required": False}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaIn, request: Request, db: DB, state: State) -> dict:
    p = await authenticate(request, db, allow_mfa_pending=True)
    raw = request.cookies.get(state.settings.session_cookie_name)
    sess = await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw or "")))
    if p is None or sess is None or not sess.mfa_pending or not p.user.totp_secret_ref:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No pending sign-in.")
    key = f"mfa|{p.user.id}"
    if throttle.blocked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed codes. Sign in again later.")
    secret = await state.vault.reveal(db, p.user.totp_secret_ref)
    if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        throttle.fail(key)
        await audit.record(db, p.actor, "auth.mfa", outcome="denied")
        await db.commit()  # keep the denial even though the request fails
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't match. Check your authenticator's clock and try again.")
    throttle.reset(key)
    sess.mfa_pending = False
    sess.expires_at = utcnow() + timedelta(hours=state.settings.session_ttl_hours)
    p.user.last_login_at = utcnow()
    await audit.record(db, p.actor, "auth.login", detail={"mfa": True})
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, db: DB, state: State) -> dict:
    raw = request.cookies.get(state.settings.session_cookie_name)
    if raw:
        sess = await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw)))
        if sess and not sess.revoked_at:
            sess.revoked_at = utcnow()
    response.delete_cookie(state.settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentPrincipal) -> MeOut:
    return me_out(principal)


@router.get("/roles")
async def roles(principal: CurrentPrincipal) -> dict:
    from warden.security.rbac import ROLE_DESCRIPTIONS

    return {
        "roles": [
            {"id": r, "description": ROLE_DESCRIPTIONS[r], "permissions": sorted(p.value for p in perms)} for r, perms in ROLES.items()
        ]
    }


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, principal: CurrentPrincipal, db: DB) -> dict:
    if principal.via != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Passwords can only be changed from an interactive session.")
    if not verify_password(principal.user.password_hash, body.current_password):
        await audit.record(db, principal.actor, "auth.password.change", outcome="denied")
        await db.commit()  # keep the denial even though the request fails
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your current password is incorrect.")
    if err := validate_password_strength(body.new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)
    principal.user.password_hash = hash_password(body.new_password)
    # Sign out every other session for this user.
    for s in await db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == principal.user.id, AuthSession.revoked_at.is_(None), AuthSession.id != principal.session_id
        )
    ):
        s.revoked_at = utcnow()
    await audit.record(db, principal.actor, "auth.password.change", target_type="user", target_id=principal.user.id)
    return {"ok": True}


# -- Preferences ------------------------------------------------------------

OVERVIEW_PANELS = ("attention", "route", "health", "changes", "controllers", "clients")


class Preferences(BaseModel):
    density: Literal["comfortable", "compact"] = "comfortable"
    theme: Literal["system", "light", "dark"] = "system"
    # Ordered panel ids; panels not listed are hidden.
    overview_panels: list[str] = Field(default_factory=lambda: list(OVERVIEW_PANELS))


def _prefs_key(user_id: str) -> str:
    return f"user:{user_id}:preferences"


@router.get("/preferences", response_model=Preferences)
async def get_preferences(principal: CurrentPrincipal, db: DB) -> Preferences:
    row = await db.get(Setting, _prefs_key(principal.user.id))
    return Preferences.model_validate(row.value) if row else Preferences()


@router.put("/preferences", response_model=Preferences)
async def put_preferences(body: Preferences, principal: CurrentPrincipal, db: DB) -> Preferences:
    unknown = [p for p in body.overview_panels if p not in OVERVIEW_PANELS]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown panels: {unknown}")
    body.overview_panels = list(dict.fromkeys(body.overview_panels))
    row = await db.get(Setting, _prefs_key(principal.user.id))
    if row is None:
        db.add(Setting(key=_prefs_key(principal.user.id), value=body.model_dump()))
    else:
        row.value = body.model_dump()
    return body


# -- MFA enrolment ----------------------------------------------------------


@router.post("/mfa/enroll")
async def mfa_enroll(principal: CurrentPrincipal, db: DB, state: State) -> dict:
    if principal.via != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "MFA can only be managed from an interactive session.")
    user = principal.user
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled.")
    secret = pyotp.random_base32()
    if user.totp_secret_ref:
        cred = await db.get(Credential, user.totp_secret_ref)
        assert cred is not None
        await state.vault.rotate(db, cred, secret)
    else:
        cred = await state.vault.store(db, kind="totp", name=f"TOTP for {user.username}", secret=secret)
        user.totp_secret_ref = cred.id
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Fabric Warden")
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/activate")
async def mfa_activate(body: MfaIn, principal: CurrentPrincipal, db: DB, state: State) -> dict:
    user = principal.user
    if not user.totp_secret_ref or user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start enrolment first.")
    secret = await state.vault.reveal(db, user.totp_secret_ref)
    if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code didn't match.")
    user.mfa_enabled = True
    await audit.record(db, principal.actor, "auth.mfa.enable", target_type="user", target_id=user.id)
    return {"ok": True}


class PasswordConfirm(BaseModel):
    password: str


@router.post("/mfa/disable")
async def mfa_disable(body: PasswordConfirm, principal: CurrentPrincipal, db: DB) -> dict:
    if principal.via != "session" or not verify_password(principal.user.password_hash, body.password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password confirmation failed.")
    principal.user.mfa_enabled = False
    await audit.record(db, principal.actor, "auth.mfa.disable", target_type="user", target_id=principal.user.id)
    return {"ok": True}


# -- API tokens -------------------------------------------------------------


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    permissions: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(db: DB, principal: Annotated[Principal, Depends(require(P.TOKENS_MANAGE_OWN))]) -> list[ApiToken]:
    rows = await db.scalars(
        select(ApiToken).where(ApiToken.user_id == principal.user.id, ApiToken.revoked_at.is_(None)).order_by(ApiToken.created_at.desc())
    )
    return list(rows)


@router.post("/tokens")
async def create_token(body: TokenIn, db: DB, principal: CurrentPrincipal) -> dict:
    if not principal.can(P.TOKENS_MANAGE_OWN) or principal.via != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tokens can only be created from an interactive session.")
    unknown = [p for p in body.permissions if p not in P._value2member_map_]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown permissions: {unknown}")
    excess = [p for p in body.permissions if P(p) not in principal.permissions]
    if excess:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"cannot grant permissions you do not hold: {excess}")
    raw = new_token("wdn_")
    tok = ApiToken(
        user_id=principal.user.id,
        name=body.name,
        prefix=raw[:10],
        token_hash=token_digest(raw),
        permissions=body.permissions,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(tok)
    await db.flush()
    await audit.record(db, principal.actor, "token.create", target_type="api_token", target_id=tok.id, detail={"name": body.name})
    return {"id": tok.id, "token": raw, "note": "Copy this token now; it will not be shown again."}


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, db: DB, principal: CurrentPrincipal) -> dict:
    tok = await db.get(ApiToken, token_id)
    if tok is None or tok.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
    tok.revoked_at = utcnow()
    await audit.record(db, principal.actor, "token.revoke", target_type="api_token", target_id=tok.id)
    return {"ok": True}

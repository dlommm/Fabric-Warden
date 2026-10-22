"""Request dependencies: database session, authentication and permission checks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.app_state import AppState
from warden.audit import Actor
from warden.db import utcnow
from warden.models import ApiToken, AuthSession, User
from warden.security.crypto import token_digest
from warden.security.rbac import P, permissions_for

CSRF_HEADER = "x-warden-csrf"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def get_state(request: Request) -> AppState:
    return request.app.state.warden


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    state: AppState = request.app.state.warden
    async with state.db.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(get_db)]
State = Annotated[AppState, Depends(get_state)]


@dataclass
class Principal:
    user: User
    permissions: frozenset[P]
    via: str  # session | token | schedule (an AI agent schedule running as its creator; token_id is the schedule id)
    session_id: str | None = None
    token_id: str | None = None
    ip: str | None = None

    @property
    def actor(self) -> Actor:
        if self.via == "token":
            return Actor("token", self.token_id, f"{self.user.username} (token)", self.ip)
        if self.via == "schedule":
            return Actor("system", self.token_id, f"{self.user.username} (AI schedule)"[:64])
        return Actor("user", self.user.id, self.user.username, self.ip)

    def can(self, perm: P) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> str | None:
    """The address the request came from, for audit and rate limits.

    The API sits behind the web container's /api proxy (and often a TLS proxy in
    front of that). Each proxy appends the address it saw to X-Forwarded-For, so
    only the last ``WARDEN_FORWARDED_HOPS`` entries are trustworthy; anything to
    their left was supplied by the client and is ignored. Trusting the whole
    header (``--forwarded-allow-ips=*``) would let a client pick its own address
    and step around per-address sign-in limits.
    """
    peer = request.client.host if request.client else None
    hops = request.app.state.warden.settings.forwarded_hops
    if hops <= 0:
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) < hops:
        return chain[0] if chain else peer
    return chain[-hops]


async def _from_token(db: AsyncSession, raw: str, request: Request) -> Principal | None:
    tok = await db.scalar(select(ApiToken).where(ApiToken.token_hash == token_digest(raw)))
    now = utcnow()
    if tok is None or tok.revoked_at or (tok.expires_at and tok.expires_at < now):
        return None
    user = await db.get(User, tok.user_id)
    if user is None or not user.is_active:
        return None
    tok.last_used_at = now
    owner = permissions_for([r.role for r in user.roles])
    perms = owner & frozenset(P(p) for p in tok.permissions if p in P._value2member_map_) if tok.permissions else owner
    return Principal(user, perms, "token", token_id=tok.id, ip=client_ip(request))


async def _from_session(db: AsyncSession, raw: str, request: Request, *, allow_mfa_pending: bool) -> Principal | None:
    sess = await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw)))
    if sess is None or sess.revoked_at or sess.expires_at < utcnow():
        return None
    if sess.mfa_pending and not allow_mfa_pending:
        return None
    user = await db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if request.method in UNSAFE and request.headers.get(CSRF_HEADER) != "1":
        # Cookie-authenticated writes must carry a custom header, which a
        # cross-site form or simple request cannot set without CORS approval.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    return Principal(user, permissions_for([r.role for r in user.roles]), "session", session_id=sess.id, ip=client_ip(request))


async def authenticate(request: Request, db: AsyncSession, *, allow_mfa_pending: bool = False) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return await _from_token(db, auth[7:].strip(), request)
    state: AppState = request.app.state.warden
    raw = request.cookies.get(state.settings.session_cookie_name)
    if raw:
        return await _from_session(db, raw, request, allow_mfa_pending=allow_mfa_pending)
    return None


async def current_principal(request: Request, db: DB) -> Principal:
    p = await authenticate(request, db)
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return p


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def _csrf_header(request: Request) -> None:
    """Unauthenticated cookie endpoints (sign-in, setup, sign-out) need the CSRF header too.

    Without it a cross-site form could sign a victim into an attacker's account
    (login CSRF) or sign them out. Bearer-token clients are exempt.
    """
    if request.method in UNSAFE and not request.headers.get("authorization", "").lower().startswith("bearer "):
        if request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")


CsrfHeader = Depends(_csrf_header)


def require(perm: P) -> Callable[..., Principal]:
    async def dep(principal: CurrentPrincipal, request: Request, db: DB) -> Principal:
        if not principal.can(perm):
            # Authorization denials are part of the audit trail (committed before raising).
            await audit.record(
                db,
                principal.actor,
                "authz.denied",
                outcome="denied",
                detail={"permission": perm.value, "method": request.method, "path": request.url.path},
            )
            await db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission {perm.value}")
        return principal

    return dep  # type: ignore[return-value]


def Allowed(perm: P) -> type[Principal]:  # noqa: N802 - reads as a type in signatures
    """`principal: Allowed(P.X)` authenticates and checks a permission."""
    return Annotated[Principal, Depends(require(perm))]  # type: ignore[return-value]


def require_module(module_id: str) -> Callable[..., None]:
    async def dep(state: State, db: DB) -> None:
        if not await state.modules.is_enabled(db, module_id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"module '{module_id}' is disabled")

    return dep  # type: ignore[return-value]

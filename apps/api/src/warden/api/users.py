"""First-run setup and user administration."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from warden import audit
from warden.api.auth import start_session
from warden.api.deps import DB, Allowed, CsrfHeader, State, client_ip
from warden.audit import Actor
from warden.db import utcnow
from warden.models import AuthSession, Controller, RoleAssignment, User
from warden.security.crypto import hash_password, validate_password_strength
from warden.security.rbac import ROLES, P

router = APIRouter(tags=["users"])


class SetupStatus(BaseModel):
    needs_owner: bool
    has_controller: bool
    demo_available: bool


@router.get("/api/setup/status", response_model=SetupStatus)
async def setup_status(db: DB, state: State) -> SetupStatus:
    users = await db.scalar(select(func.count()).select_from(User))
    controllers = await db.scalar(select(func.count()).select_from(Controller))
    return SetupStatus(needs_owner=users == 0, has_controller=bool(controllers), demo_available=state.settings.demo_controller_enabled)


class OwnerIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(default="", max_length=128)
    email: EmailStr | None = None
    password: str = Field(max_length=1024)


_setup_lock = asyncio.Lock()


@router.post("/api/setup/owner", status_code=201, dependencies=[CsrfHeader])
async def create_owner(body: OwnerIn, request: Request, response: Response, db: DB, state: State) -> dict:
    # Serialise first-run creation: only possible while no users exist. The lock stops two
    # concurrent requests (in this process) from both seeing zero users and creating two owners.
    async with _setup_lock:
        return await _create_owner(body, request, response, db, state)


async def _create_owner(body: OwnerIn, request: Request, response: Response, db: DB, state: State) -> dict:
    if await db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed.")
    if err := validate_password_strength(body.password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)
    user = User(
        username=body.username,
        display_name=body.display_name or body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        roles=[RoleAssignment(role="owner")],
    )
    db.add(user)
    await db.flush()
    await audit.record(
        db, Actor("user", user.id, user.username, client_ip(request)), "setup.owner_created", target_type="user", target_id=user.id
    )
    await start_session(db, state, request, response, user, mfa_pending=False)
    await db.commit()  # commit inside the lock so the next request sees the owner
    return {"id": user.id}


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    roles: list[str]
    is_active: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        email=u.email,
        roles=sorted({r.role for r in u.roles}),
        is_active=u.is_active,
        mfa_enabled=u.mfa_enabled,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


@router.get("/api/users", response_model=list[UserOut])
async def list_users(db: DB, principal: Allowed(P.USERS_READ)) -> list[UserOut]:
    return [user_out(u) for u in await db.scalars(select(User).order_by(User.username))]


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(default="", max_length=128)
    email: EmailStr | None = None
    password: str = Field(max_length=1024)
    roles: list[str] = Field(min_length=1)


def _check_roles(roles: list[str], granter_roles: set[str]) -> None:
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown roles: {bad}")
    if "owner" in roles and "owner" not in granter_roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can grant the owner role.")


@router.post("/api/users", status_code=201, response_model=UserOut)
async def create_user(body: UserIn, db: DB, principal: Allowed(P.USERS_MANAGE)) -> UserOut:
    _check_roles(body.roles, {r.role for r in principal.user.roles})
    if err := validate_password_strength(body.password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)
    if await db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is taken.")
    user = User(
        username=body.username,
        display_name=body.display_name or body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        roles=[RoleAssignment(role=r) for r in sorted(set(body.roles))],
    )
    db.add(user)
    await db.flush()
    await audit.record(db, principal.actor, "user.create", target_type="user", target_id=user.id, detail={"roles": body.roles})
    await db.refresh(user, ["roles"])
    return user_out(user)


class UserPatch(BaseModel):
    display_name: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


@router.patch("/api/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch, db: DB, principal: Allowed(P.USERS_MANAGE)) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    granter = {r.role for r in principal.user.roles}
    target_is_owner = any(r.role == "owner" for r in user.roles)
    if target_is_owner and "owner" not in granter:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can modify an owner.")
    changes: dict = {}
    if body.display_name is not None:
        user.display_name = body.display_name
        changes["display_name"] = body.display_name
    if body.roles is not None:
        _check_roles(body.roles, granter)
        if target_is_owner and "owner" not in body.roles and await _owner_count(db) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "At least one owner must remain.")
        user.roles = [RoleAssignment(role=r) for r in sorted(set(body.roles))]
        changes["roles"] = body.roles
    if body.is_active is not None:
        if user.id == principal.user.id and not body.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "You can't deactivate your own account.")
        if target_is_owner and not body.is_active and await _owner_count(db) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "At least one owner must remain.")
        user.is_active = body.is_active
        changes["is_active"] = body.is_active
        if not body.is_active:
            for s in await db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
                s.revoked_at = utcnow()
    await db.flush()
    await audit.record(db, principal.actor, "user.update", target_type="user", target_id=user.id, detail=changes)
    await db.refresh(user, ["roles"])
    return user_out(user)


async def _owner_count(db: DB) -> int:
    return (
        await db.scalar(
            select(func.count(func.distinct(RoleAssignment.user_id)))
            .join(User, User.id == RoleAssignment.user_id)
            .where(RoleAssignment.role == "owner", User.is_active.is_(True))
        )
        or 0
    )

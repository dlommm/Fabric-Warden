"""Connection management shared by the UniFi Protect and Home Assistant routers.

Each connection is one API endpoint plus a credential in the vault (a Protect
API key or a Home Assistant long-lived access token). Responses only ever show
the credential's fingerprint. Every change is audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, State
from warden.integrations.ecosystem import collect, sync_connection
from warden.integrations.hosts.clients import HostError
from warden.models import Credential, HaEntity, IntegrationConnection, ProtectDevice
from warden.security.outbound import validate_optional_url
from warden.security.rbac import P

Manager = Allowed(P.INTEGRATIONS_MANAGE)
Reader = Allowed(P.INVENTORY_READ)

HINTS = {
    "unifi-protect": {
        "auth": "The API key was refused. Create one on the console that runs Protect (Settings › Control Plane › Integrations); "
        "a key from a separate gateway does not work on a UNVR.",
        "unreachable": "The console did not answer. Check the URL (https://<console>) and the firewall between Fabric Warden and it.",
        "not_found": "The console answered but has no Protect Integration API. It needs Protect 5.3 or later.",
    },
    "home-assistant": {
        "auth": "The token was refused. Create a long-lived access token on your Home Assistant profile, under Security.",
        "unreachable": "Home Assistant did not answer. Check the URL and port (8123, or 80 behind the Supervisor).",
        "not_found": "The REST API is missing. It needs the api integration (loaded by default_config).",
    },
    "*": {
        "tls": "The certificate could not be verified. Paste the CA certificate, or turn verification off on a trusted network.",
        "protocol": "The service answered with an error. See the detail.",
        "refused": "Fabric Warden only sends read requests.",
        "error": "The connection failed. See the detail.",
    },
}


class ConnIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=512, pattern=r"^https?://")
    secret: str = Field(min_length=1, max_length=4096)
    verify_tls: bool = True
    ca_bundle_pem: str | None = Field(default=None, max_length=65536)

    @field_validator("base_url")
    @classmethod
    def _safe_url(cls, v: str | None) -> str | None:
        return validate_optional_url(v)


class ConnCreate(ConnIn):
    name: str = Field(min_length=1, max_length=128)


class ConnPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    verify_tls: bool | None = None
    secret: str | None = Field(default=None, min_length=1, max_length=4096)


def hint(kind: str, error_kind: str) -> str:
    return HINTS.get(kind, {}).get(error_kind) or HINTS["*"].get(error_kind) or HINTS["*"]["error"]


async def conn_out(db: AsyncSession, c: IntegrationConnection) -> dict[str, Any]:
    fp = None
    if c.credential_id:
        cred = await db.get(Credential, c.credential_id)
        fp = cred.fingerprint if cred else None
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "base_url": c.base_url,
        "credential_fingerprint": fp,
        "verify_tls": c.verify_tls,
        "enabled": c.enabled,
        "status": c.status,
        "status_detail": c.status_detail,
        "version": c.version,
        "last_sync_at": c.last_sync_at,
        "is_demo": c.is_demo,
    }


async def purge(db: AsyncSession, conn_ids: list[str]) -> None:
    await db.execute(delete(ProtectDevice).where(ProtectDevice.connection_id.in_(conn_ids)))
    await db.execute(delete(HaEntity).where(HaEntity.connection_id.in_(conn_ids)))


def add_connection_routes(router: APIRouter, kind: str, audit_prefix: str, demo: tuple[str, str, str] | None = None) -> None:
    """Connection CRUD, test, sync and demo routes for one integration kind.

    demo = (name, base_url, secret) of the simulator-backed demo connection.
    """

    async def probe(state: State, body: ConnIn) -> dict[str, Any]:
        tmp = IntegrationConnection(
            name="test", kind=kind, base_url=body.base_url.rstrip("/"), verify_tls=body.verify_tls, ca_bundle_pem=body.ca_bundle_pem
        )
        try:
            snap = await collect(state, tmp, body.secret)
        except HostError as e:
            return {"ok": False, "error_kind": e.kind, "error": str(e), "hint": hint(kind, e.kind)}
        out: dict[str, Any] = {"ok": True, "version": snap.version, "notes": snap.notes}
        if hasattr(snap, "items"):
            out["cameras"] = sum(i.kind == "camera" for i in snap.items)  # type: ignore[union-attr]
        else:
            out["entities"] = len(snap.entities)  # type: ignore[union-attr]
            out["states_seen"] = snap.total_states  # type: ignore[union-attr]
        return out

    async def get_conn(db: AsyncSession, cid: str) -> IntegrationConnection:
        c = await db.get(IntegrationConnection, cid)
        if c is None or c.kind != kind:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
        return c

    @router.get("/connections")
    async def list_connections(db: DB, principal: Reader) -> dict:
        rows = await db.scalars(
            select(IntegrationConnection).where(IntegrationConnection.kind == kind).order_by(IntegrationConnection.name)
        )
        return {"items": [await conn_out(db, c) for c in rows]}

    @router.post("/connections/test")
    async def test_connection(body: ConnIn, state: State, principal: Manager) -> dict:
        return await probe(state, body)

    @router.post("/connections", status_code=status.HTTP_201_CREATED)
    async def create_connection(body: ConnCreate, db: DB, state: State, principal: Manager) -> dict:
        result = await probe(state, body)
        if not result["ok"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result)
        c = IntegrationConnection(
            name=body.name, kind=kind, base_url=body.base_url.rstrip("/"), verify_tls=body.verify_tls, ca_bundle_pem=body.ca_bundle_pem
        )
        cred = await state.vault.store(db, kind=f"{kind}-secret", name=f"{kind} credential for {body.name}", secret=body.secret)
        c.credential_id = cred.id
        db.add(c)
        await db.flush()
        await audit.record(
            db, principal.actor, f"{audit_prefix}.connection.create", target_type="integration_connection", target_id=c.id,
            detail={"kind": kind, "base_url": c.base_url},
        )  # fmt: skip
        await sync_connection(db, state, c)
        return await conn_out(db, c)

    @router.patch("/connections/{cid}")
    async def update_connection(cid: str, body: ConnPatch, db: DB, state: State, principal: Manager) -> dict:
        c = await get_conn(db, cid)
        fields = body.model_dump(exclude_unset=True, exclude={"secret"})
        for k, v in fields.items():
            setattr(c, k, v)
        if body.secret:
            if c.credential_id and (cred := await db.get(Credential, c.credential_id)):
                await state.vault.rotate(db, cred, body.secret)
            else:
                cred = await state.vault.store(db, kind=f"{kind}-secret", name=f"{kind} credential for {c.name}", secret=body.secret)
                c.credential_id = cred.id
            fields["secret_rotated"] = True
        await audit.record(
            db, principal.actor, f"{audit_prefix}.connection.update", target_type="integration_connection", target_id=c.id, detail=fields
        )
        return await conn_out(db, c)

    @router.delete("/connections/{cid}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_connection(cid: str, db: DB, principal: Manager) -> None:
        c = await get_conn(db, cid)
        await purge(db, [c.id])
        await audit.record(
            db, principal.actor, f"{audit_prefix}.connection.delete", target_type="integration_connection", target_id=c.id,
            detail={"name": c.name},
        )  # fmt: skip
        await db.delete(c)

    @router.post("/connections/{cid}/sync")
    async def sync_now(cid: str, db: DB, state: State, principal: Manager) -> dict:
        return await sync_connection(db, state, await get_conn(db, cid))

    if demo is None:
        return
    demo_name, demo_url, demo_secret = demo

    @router.post("/demo", status_code=status.HTTP_201_CREATED)
    async def create_demo(db: DB, state: State, principal: Manager) -> dict:
        if not state.settings.demo_controller_enabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo data is disabled on this installation.")
        q = select(IntegrationConnection).where(IntegrationConnection.kind == kind, IntegrationConnection.is_demo.is_(True))
        if await db.scalar(q):
            raise HTTPException(status.HTTP_409_CONFLICT, "The demo connection already exists.")
        c = IntegrationConnection(name=demo_name, kind=kind, base_url=demo_url, is_demo=True)
        cred = await state.vault.store(db, kind=f"{kind}-secret", name=f"demo {kind} credential", secret=demo_secret)
        c.credential_id = cred.id
        db.add(c)
        await db.flush()
        await sync_connection(db, state, c)
        await audit.record(db, principal.actor, f"{audit_prefix}.demo.create", target_type="integration_connection", target_id=c.id)
        return await conn_out(db, c)

    @router.delete("/demo")
    async def remove_demo(db: DB, principal: Manager) -> dict:
        q = select(IntegrationConnection).where(IntegrationConnection.kind == kind, IntegrationConnection.is_demo.is_(True))
        conns = list(await db.scalars(q))
        await purge(db, [c.id for c in conns])
        for c in conns:
            await db.delete(c)
        await audit.record(db, principal.actor, f"{audit_prefix}.demo.remove", detail={"connections": len(conns)})
        return {"removed": len(conns)}

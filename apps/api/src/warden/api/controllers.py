"""Controller connections: test, create, update, sync."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import func, select

from warden import audit
from warden.api.deps import DB, Allowed, State
from warden.app_state import SIMULATOR_BASE_URL
from warden.integrations.unifi import capabilities
from warden.integrations.unifi.client import UniFiError
from warden.integrations.unifi.simulator import SIM_API_KEY
from warden.models import Client, Controller, Credential, Device, Site
from warden.security.outbound import check_url
from warden.security.rbac import P

router = APIRouter(prefix="/api/controllers", tags=["controllers"])

HINTS = {
    "auth": "The controller rejected the API key. Create a key under Settings → Control Plane → Integrations on the console and paste it here.",
    "unreachable": "The controller didn't answer. Check the address, port and that this server can reach it.",
    "tls": "TLS verification failed. UniFi consoles use self-signed certificates by default: paste the console's CA certificate, or turn off verification on a trusted network.",
    "not_found": "The Integration API wasn't found at this address. It needs UniFi Network 9.1 or newer, reached through the console (https://<console-ip>).",
    "protocol": "The address answered, but not like a UniFi console. Check that it points at the console itself.",
    "error": "The controller returned an unexpected error.",
}


class ControllerOut(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    verify_tls: bool
    has_ca_bundle: bool
    write_mode: str
    is_demo: bool
    poll_interval_seconds: int
    status: str
    status_detail: str
    application_version: str | None
    capabilities: dict[str, Any]
    capabilities_checked_at: datetime | None
    last_sync_at: datetime | None
    credential_fingerprint: str | None
    site_count: int
    device_count: int
    client_count: int


async def controller_out(db: DB, c: Controller) -> ControllerOut:
    fp = None
    if c.credential_id:
        cred = await db.get(Credential, c.credential_id)
        fp = cred.fingerprint if cred else None
    site_ids = select(Site.id).where(Site.controller_id == c.id)
    return ControllerOut(
        id=c.id,
        name=c.name,
        kind=c.kind,
        base_url=c.base_url,
        verify_tls=c.verify_tls,
        has_ca_bundle=bool(c.ca_bundle_pem),
        write_mode=c.write_mode,
        is_demo=c.is_demo,
        poll_interval_seconds=c.poll_interval_seconds,
        status=c.status,
        status_detail=c.status_detail,
        application_version=c.application_version,
        capabilities=c.capabilities or {},
        capabilities_checked_at=c.capabilities_checked_at,
        last_sync_at=c.last_sync_at,
        credential_fingerprint=fp,
        site_count=await db.scalar(select(func.count()).select_from(Site).where(Site.controller_id == c.id)) or 0,
        device_count=await db.scalar(select(func.count()).select_from(Device).where(Device.site_id.in_(site_ids), Device.present.is_(True)))
        or 0,
        client_count=await db.scalar(select(func.count()).select_from(Client).where(Client.site_id.in_(site_ids), Client.present.is_(True)))
        or 0,
    )


class ConnectionIn(BaseModel):
    base_url: HttpUrl
    api_key: str = Field(min_length=8, max_length=512)
    verify_tls: bool = True
    ca_bundle_pem: str | None = Field(default=None, max_length=65536)

    @field_validator("base_url")
    @classmethod
    def _safe_url(cls, v: HttpUrl) -> HttpUrl:
        check_url(str(v))
        return v


class ControllerIn(ConnectionIn):
    name: str = Field(min_length=1, max_length=128)
    poll_interval_seconds: int = Field(default=60, ge=15, le=3600)


async def probe(state: State, c: Controller, api_key: str) -> dict[str, Any]:
    try:
        async with state.unifi_client(c, api_key) as api:
            info = await api.info()
            sites = await api.sites()
    except UniFiError as exc:
        return {"ok": False, "error_kind": exc.kind, "error": str(exc), "hint": HINTS.get(exc.kind, HINTS["error"])}
    version = info.get("applicationVersion")
    report = capabilities.evaluate(version)
    return {
        "ok": True,
        "application_version": version,
        "sites": [{"id": s["id"], "name": s.get("name")} for s in sites],
        "capabilities": report,
    }


@router.get("", response_model=list[ControllerOut])
async def list_controllers(db: DB, principal: Allowed(P.CONTROLLERS_READ)) -> list[ControllerOut]:
    return [await controller_out(db, c) for c in await db.scalars(select(Controller).order_by(Controller.created_at))]


@router.post("/test")
async def test_connection(body: ConnectionIn, state: State, principal: Allowed(P.CONTROLLERS_MANAGE)) -> dict:
    tmp = Controller(
        name="test", kind="unifi-network", base_url=str(body.base_url), verify_tls=body.verify_tls, ca_bundle_pem=body.ca_bundle_pem
    )
    return await probe(state, tmp, body.api_key)


@router.post("", status_code=201, response_model=ControllerOut)
async def create_controller(body: ControllerIn, db: DB, state: State, principal: Allowed(P.CONTROLLERS_MANAGE)) -> ControllerOut:
    c = Controller(
        name=body.name,
        kind="unifi-network",
        base_url=str(body.base_url).rstrip("/"),
        verify_tls=body.verify_tls,
        ca_bundle_pem=body.ca_bundle_pem,
        poll_interval_seconds=body.poll_interval_seconds,
    )
    result = await probe(state, c, body.api_key)
    if not result["ok"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result)
    cred = await state.vault.store(db, kind="unifi-api-key", name=f"API key for {body.name}", secret=body.api_key)
    c.credential_id = cred.id
    c.application_version = result["application_version"]
    c.capabilities = result["capabilities"]
    db.add(c)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "controller.create",
        target_type="controller",
        target_id=c.id,
        detail={"base_url": c.base_url, "version": c.application_version, "credential_fingerprint": cred.fingerprint},
    )
    return await controller_out(db, c)


@router.post("/demo", status_code=201, response_model=ControllerOut)
async def create_demo(db: DB, state: State, principal: Allowed(P.CONTROLLERS_MANAGE)) -> ControllerOut:
    if not state.settings.demo_controller_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The demo controller is disabled on this installation.")
    if await db.scalar(select(Controller).where(Controller.kind == "unifi-simulator")):
        raise HTTPException(status.HTTP_409_CONFLICT, "A demo controller already exists.")
    cred = await state.vault.store(db, kind="unifi-api-key", name="Simulator key", secret=SIM_API_KEY)
    c = Controller(
        name="Demo Home Lab (simulated)",
        kind="unifi-simulator",
        base_url=SIMULATOR_BASE_URL,
        credential_id=cred.id,
        is_demo=True,
        poll_interval_seconds=60,
    )
    db.add(c)
    await db.flush()
    await audit.record(db, principal.actor, "controller.create", target_type="controller", target_id=c.id, detail={"demo": True})
    return await controller_out(db, c)


@router.get("/{controller_id}", response_model=ControllerOut)
async def get_controller(controller_id: str, db: DB, principal: Allowed(P.CONTROLLERS_READ)) -> ControllerOut:
    c = await db.get(Controller, controller_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "controller not found")
    return await controller_out(db, c)


class ControllerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    verify_tls: bool | None = None
    ca_bundle_pem: str | None = Field(default=None, max_length=65536)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)


@router.patch("/{controller_id}", response_model=ControllerOut)
async def update_controller(
    controller_id: str, body: ControllerPatch, db: DB, state: State, principal: Allowed(P.CONTROLLERS_MANAGE)
) -> ControllerOut:
    c = await db.get(Controller, controller_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "controller not found")
    changed = body.model_dump(exclude_unset=True, exclude={"api_key", "ca_bundle_pem"})
    for k, v in changed.items():
        setattr(c, k, v)
    if "ca_bundle_pem" in body.model_fields_set:
        c.ca_bundle_pem = body.ca_bundle_pem or None
        changed["ca_bundle_pem"] = "updated"
    if body.api_key:
        if c.is_demo:
            raise HTTPException(status.HTTP_409_CONFLICT, "The demo controller's key can't be changed.")
        result = await probe(state, c, body.api_key)
        if not result["ok"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result)
        cred = await db.get(Credential, c.credential_id) if c.credential_id else None
        if cred:
            await state.vault.rotate(db, cred, body.api_key)
        else:
            cred = await state.vault.store(db, kind="unifi-api-key", name=f"API key for {c.name}", secret=body.api_key)
            c.credential_id = cred.id
        changed["api_key"] = f"rotated (fingerprint {cred.fingerprint})"
    await audit.record(db, principal.actor, "controller.update", target_type="controller", target_id=c.id, detail=changed)
    return await controller_out(db, c)


@router.delete("/{controller_id}")
async def delete_controller(controller_id: str, db: DB, principal: Allowed(P.CONTROLLERS_MANAGE)) -> dict:
    c = await db.get(Controller, controller_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "controller not found")
    await audit.record(db, principal.actor, "controller.delete", target_type="controller", target_id=c.id, detail={"name": c.name})
    cred_id = c.credential_id
    await db.delete(c)
    await db.flush()
    if cred_id and (cred := await db.get(Credential, cred_id)):
        await db.delete(cred)
    return {"ok": True}


@router.post("/{controller_id}/sync")
async def sync_now(controller_id: str, db: DB, state: State, principal: Allowed(P.JOBS_RUN)) -> dict:
    if await db.get(Controller, controller_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "controller not found")
    await audit.record(
        db, principal.actor, "job.run", target_type="job", target_id="unifi-network.sync", detail={"controller_id": controller_id}
    )
    await db.commit()
    run = await state.scheduler.run("unifi-network.sync", "manual", {"controller_id": controller_id})
    return {"run_id": run.id, "status": run.status, "summary": run.summary, "error": run.error}

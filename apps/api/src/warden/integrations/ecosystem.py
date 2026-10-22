"""Collect and sync for the ecosystem integrations (UniFi Protect, Home Assistant).

One code path per kind: collect() talks to the API (or the in-process
simulator for demo connections) and sync_connection() upserts observed rows
with provenance, marking rows the API no longer reports as not present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import utcnow
from warden.integrations.homeassistant import client as ha
from warden.integrations.hosts.clients import HostError, ReadOnlyHTTP
from warden.integrations.protect import client as protect
from warden.models import HaEntity, IntegrationConnection, ProtectDevice

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

KINDS = ("unifi-protect", "home-assistant")
SOURCE_NAME = {"unifi-protect": "unifi-protect-integration-api", "home-assistant": "home-assistant-rest-api"}


def _api(state: AppState, conn: IntegrationConnection, secret: str | None) -> ReadOnlyHTTP:
    if conn.kind == "unifi-protect":
        base, hdrs = protect.api_base(conn.base_url), protect.headers(secret)
    else:
        base, hdrs = conn.base_url.rstrip("/"), ha.headers(secret)
    return ReadOnlyHTTP(
        base, headers=hdrs, verify_tls=conn.verify_tls, ca_bundle_pem=conn.ca_bundle_pem, transport=state.integration_transport(conn)
    )


async def collect(state: AppState, conn: IntegrationConnection, secret: str | None) -> protect.ProtectSnapshot | ha.HaSnapshot:
    if conn.kind not in KINDS:
        raise HostError(f"Unknown connection kind {conn.kind}", kind="error")
    async with _api(state, conn, secret) as api:
        if conn.kind == "unifi-protect":
            return await protect.collect_protect(api)
        return await ha.collect_home_assistant(api)


async def sync_connection(db: AsyncSession, state: AppState, conn: IntegrationConnection) -> dict[str, Any]:
    secret = await state.vault.reveal(db, conn.credential_id) if conn.credential_id else None
    now = utcnow()
    try:
        snap = await collect(state, conn, secret)
    except HostError as e:
        conn.status = "unreachable" if e.kind in ("unreachable", "tls") else "degraded"
        conn.status_detail = str(e)[:500]
        return {"connection": conn.id, "status": conn.status, "error": str(e)}

    source = f"{'simulator' if conn.is_demo else SOURCE_NAME[conn.kind]}@{snap.version or 'unknown'}"
    common = {"source": source, "is_demo": conn.is_demo, "observed_at": now, "present": True}
    seen: set[str] = set()
    if isinstance(snap, protect.ProtectSnapshot):
        before = {d.external_id: d for d in await db.scalars(select(ProtectDevice).where(ProtectDevice.connection_id == conn.id))}
        for it in snap.items:
            row = before.get(it.external_id)
            if row is None:
                row = ProtectDevice(connection_id=conn.id, external_id=it.external_id, first_seen_at=now)
                db.add(row)
                before[it.external_id] = row
            for k, v in {
                **common,
                "kind": it.kind,
                "name": it.name[:128],
                "model_key": it.model_key[:32],
                "type": it.type[:64],
                "mac_address": it.mac_address,
                "state": it.state[:16],
                "extra": it.extra,
                "raw": it.raw,
            }.items():
                setattr(row, k, v)
            seen.add(it.external_id)
        rows: list[Any] = list(before.values())
        counts = {"cameras": sum(i.kind == "camera" for i in snap.items), "nvrs": sum(i.kind == "nvr" for i in snap.items)}
    else:
        before_e = {e.external_id: e for e in await db.scalars(select(HaEntity).where(HaEntity.connection_id == conn.id))}
        for ed in snap.entities:
            row_e = before_e.get(ed.external_id)
            if row_e is None:
                row_e = HaEntity(connection_id=conn.id, external_id=ed.external_id, first_seen_at=now)
                db.add(row_e)
                before_e[ed.external_id] = row_e
            for k, v in {
                **common,
                "entity_id": ed.entity_id,
                "domain": ed.domain,
                "category": ed.category,
                "category_basis": ed.basis,
                "name": ed.name,
                "state": ed.state,
                "unit": ed.unit,
                "device_class": ed.device_class,
                "attributes": ed.attributes,
                "last_changed": ed.last_changed,
                "raw": {},
            }.items():
                setattr(row_e, k, v)
            seen.add(ed.external_id)
        rows = list(before_e.values())
        counts = {"entities": len(snap.entities), "states_seen": snap.total_states}
    for r in rows:
        if r.external_id not in seen:
            r.present = False
    conn.status = "degraded" if snap.notes else "ok"
    conn.status_detail = "; ".join(snap.notes)[:500]
    conn.version = snap.version or None
    conn.last_sync_at = now
    await db.flush()
    return {"connection": conn.id, "status": conn.status, "version": snap.version, **counts, "notes": snap.notes}


async def _sync_kind(ctx: JobContext, kind: str) -> dict[str, Any]:
    q = select(IntegrationConnection).where(IntegrationConnection.kind == kind, IntegrationConnection.enabled.is_(True))
    if cid := ctx.params.get("connection_id"):
        q = select(IntegrationConnection).where(IntegrationConnection.id == cid, IntegrationConnection.kind == kind)
    return {"connections": [await sync_connection(ctx.db, ctx.state, c) for c in await ctx.db.scalars(q)]}


async def protect_sync_job(ctx: JobContext) -> dict[str, Any]:
    return await _sync_kind(ctx, "unifi-protect")


async def home_assistant_sync_job(ctx: JobContext) -> dict[str, Any]:
    return await _sync_kind(ctx, "home-assistant")

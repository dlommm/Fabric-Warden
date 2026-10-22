"""Poll SNMP targets, derive rates from counter deltas and record port metrics."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import metrics
from warden.integrations.snmp.client import Credentials, PysnmpSession, Session, Snapshot, SnmpError, collect, port_number
from warden.integrations.snmp.simulator import SimulatedAgent
from warden.models import Device, Port, SnmpInterface, SnmpProfile, SnmpTarget

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

log = logging.getLogger(__name__)

# A 32-bit counter that went backwards by less than this wrapped; more means the agent reset it.
WRAP32 = 2**32


def credentials(profile: SnmpProfile, secret: str | None) -> Credentials:
    data: dict[str, str] = json.loads(secret) if secret else {}
    return Credentials(
        version=profile.version,
        username=profile.username,
        auth_protocol=profile.auth_protocol,
        auth_key=data.get("auth_key", ""),
        priv_protocol=profile.priv_protocol,
        priv_key=data.get("priv_key", ""),
        community=data.get("community", ""),
    )


async def session_for(db: AsyncSession, state: AppState, target: SnmpTarget, profile: SnmpProfile, creds: Credentials) -> Session:
    """Demo targets answer from the in-process simulator built from the device's ports; real ones use UDP."""
    if target.is_demo:
        device = await db.get(Device, target.device_id) if target.device_id else None
        if device is None:
            raise SnmpError("The demo device for this target is gone", kind="unreachable")
        ports = await db.scalars(select(Port).where(Port.device_id == device.id).order_by(Port.idx))
        shape = {
            "name": device.name,
            "model": device.model,
            "mac_address": device.mac_address,
            "ports": [
                {"idx": p.idx, "connector": p.connector, "state": p.state, "speed_mbps": p.speed_mbps, "max_speed_mbps": p.max_speed_mbps}
                for p in ports
            ],
        }
        return SimulatedAgent(state.snmp_sim, shape, creds)
    if state.snmp_session is not None:
        return state.snmp_session(target.host, target.port, creds)
    return PysnmpSession(target.host, target.port, creds)


async def probe(db: AsyncSession, state: AppState, target: SnmpTarget, profile: SnmpProfile, creds: Credentials) -> Snapshot:
    session = await session_for(db, state, target, profile, creds)
    try:
        return await collect(session)
    finally:
        if isinstance(session, PysnmpSession):
            session.close()


def _delta(new: int | None, old: int | None, bits: int) -> int | None:
    if new is None or old is None:
        return None
    if new >= old:
        return new - old
    if bits == 32 and old - new > WRAP32 // 2:
        return new + WRAP32 - old
    return None  # reset: no rate this interval


def rates(prev: dict[str, Any], cur: dict[str, int], dt: float) -> dict[str, float | None]:
    out: dict[str, float | None] = {"rx_bps": None, "tx_bps": None, "errors_per_min": None, "discards_per_min": None}
    if dt <= 0 or not prev:
        return out
    rx = _delta(cur.get("in_octets"), prev.get("in_octets"), 64)
    tx = _delta(cur.get("out_octets"), prev.get("out_octets"), 64)
    out["rx_bps"] = round(rx * 8 / dt, 1) if rx is not None else None
    out["tx_bps"] = round(tx * 8 / dt, 1) if tx is not None else None
    for name, keys in (("errors_per_min", ("in_errors", "out_errors")), ("discards_per_min", ("in_discards", "out_discards"))):
        parts = [_delta(cur.get(k), prev.get(k), 32) for k in keys]
        if all(p is not None for p in parts):
            out[name] = round(sum(p for p in parts if p is not None) * 60 / dt, 2)
    return out


async def _device_by_mac(db: AsyncSession, macs: set[str]) -> Device | None:
    """The one live device whose MAC an interface reports: a gateway is listed under its WAN address, not the one polled."""
    if not macs:
        return None
    found = list(
        await db.scalars(
            select(Device).where(func.lower(Device.mac_address).in_(macs), Device.present.is_(True), Device.is_demo.is_(False))
        )
    )
    return found[0] if len(found) == 1 else None


async def poll_target(db: AsyncSession, state: AppState, target: SnmpTarget) -> dict[str, Any]:
    profile = await db.get(SnmpProfile, target.profile_id)
    if profile is None:
        return {"target": target.id, "status": "unknown", "error": "profile missing"}
    if target.device_id is None and not target.is_demo:
        # Added by host before its controller was synced: attach to the live device at that address once it appears.
        target.device_id = await db.scalar(
            select(Device.id).where(Device.ip_address == target.host, Device.present.is_(True), Device.is_demo.is_(False))
        )
    secret = await state.vault.reveal(db, profile.credential_id) if profile.credential_id else None
    try:
        snap = await probe(db, state, target, profile, credentials(profile, secret))
    except SnmpError as e:
        target.status = "unreachable" if e.kind == "unreachable" else "degraded"
        target.status_detail, target.error_kind = str(e)[:500], e.kind
        return {"target": target.id, "status": target.status, "error": str(e)}

    now = datetime.fromtimestamp(snap.read_at, UTC)
    restarted = target.sys_uptime_s is not None and snap.uptime_s is not None and snap.uptime_s < target.sys_uptime_s
    existing = {i.external_id: i for i in await db.scalars(select(SnmpInterface).where(SnmpInterface.target_id == target.id))}
    source = f"{'simulator' if target.is_demo else 'snmp'}@{profile.version}"
    seen: set[str] = set()
    samples: list[metrics.Sample] = []
    device = await db.get(Device, target.device_id) if target.device_id else None
    if device is None and not target.is_demo:
        device = await _device_by_mac(db, {r.mac_address.lower() for r in snap.interfaces if r.mac_address})
        target.device_id = device.id if device else None
    site_id = device.site_id if device else None
    for row in snap.interfaces:
        ext = str(row.if_index)
        seen.add(ext)
        iface = existing.get(ext)
        if iface is None:
            iface = SnmpInterface(target_id=target.id, external_id=ext, if_index=row.if_index, first_seen_at=now, counters={})
            db.add(iface)
        prev_t = iface.counters_at.timestamp() if iface.counters_at else None
        r = rates({} if restarted or prev_t is None else iface.counters, row.counters, snap.read_at - (prev_t or snap.read_at))
        iface.source, iface.is_demo, iface.observed_at, iface.present, iface.raw = source, target.is_demo, now, True, {}
        iface.name, iface.descr, iface.alias, iface.if_type = row.name, row.descr, row.alias, row.if_type
        iface.mac_address, iface.admin_up, iface.oper_status, iface.speed_mbps = (
            row.mac_address,
            row.admin_up,
            row.oper_status,
            row.speed_mbps,
        )
        iface.port_idx = port_number(row.name, row.descr, row.if_index) if row.physical else None
        iface.counters, iface.counters_at = row.counters, now
        iface.rx_bps, iface.tx_bps = r["rx_bps"], r["tx_bps"]
        iface.errors_per_min, iface.discards_per_min = r["errors_per_min"], r["discards_per_min"]
        if row.physical and row.oper_status == "up":
            await db.flush()
            label = f"{device.name if device else target.name} · {row.name or row.descr}"
            for metric, value in (
                ("port.rx_bps", r["rx_bps"]),
                ("port.tx_bps", r["tx_bps"]),
                ("port.errors", r["errors_per_min"]),
                ("port.discards", r["discards_per_min"]),
            ):
                if value is not None:
                    samples.append(metrics.Sample(metric, "interface", iface.id, label, value, site_id, target.is_demo))
    for ext, iface in existing.items():
        if ext not in seen:
            iface.present = False

    target.status, target.status_detail, target.error_kind = "ok", "", None
    target.sys_name, target.sys_descr, target.sys_uptime_s = snap.sys_name, snap.sys_descr, snap.uptime_s
    target.last_poll_at = now
    await db.flush()
    await metrics.record(db, samples, now)
    return {"target": target.id, "status": "ok", "interfaces": len(seen), "restarted": restarted}


async def poll_job(ctx: JobContext) -> dict[str, Any]:
    q = select(SnmpTarget).where(SnmpTarget.enabled.is_(True))
    if tid := ctx.params.get("target_id"):
        q = select(SnmpTarget).where(SnmpTarget.id == tid)
    return {"targets": [await poll_target(ctx.db, ctx.state, t) for t in await ctx.db.scalars(q)]}

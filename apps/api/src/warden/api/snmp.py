"""SNMP port counters: credential profiles, targets, interfaces and findings.

Polling is read-only (see integrations/snmp/client.py). Passphrases and
communities live in the credential vault; responses only show a fingerprint.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.integrations.snmp.client import AUTH_PROTOCOLS, PRIV_PROTOCOLS, SnmpError, is_physical
from warden.integrations.snmp.simulator import SIM_PASSWORD, SIM_USER
from warden.integrations.snmp.sync import credentials, poll_target, probe
from warden.models import Credential, Device, Port, SnmpInterface, SnmpProfile, SnmpTarget
from warden.security.rbac import P

router = APIRouter(prefix="/api/snmp", tags=["snmp"], dependencies=[Depends(require_module("snmp"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.SNMP_MANAGE)

STALE = timedelta(minutes=5)
UTIL_WARN = 80.0
# Models Ubiquiti documents as having no SNMP agent.
NO_SNMP = ("flex", "ultra")

HINTS = {
    "auth": (
        "The agent refused the credentials. UniFi uses SHA authentication and AES-128 privacy with the same password for both; "
        "check the username and password set under Settings > CyberSecure > Traffic Logging."
    ),
    "unreachable": (
        "No answer on UDP 161. Check that SNMP is enabled in UniFi, the address is the device's management IP, "
        "and no firewall policy blocks Fabric Warden's host from the management network."
    ),
    "protocol": "The agent answered with an error. See the detail.",
    "error": "The request failed. See the detail.",
}


# -- profiles --------------------------------------------------------------------------


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: Literal["v3", "v2c"] = "v3"
    username: str = Field(default="", max_length=64)
    auth_protocol: Literal[AUTH_PROTOCOLS] = "SHA"  # type: ignore[valid-type]
    priv_protocol: Literal[PRIV_PROTOCOLS] = "AES"  # type: ignore[valid-type]
    # v3: the authentication passphrase; also the privacy passphrase unless priv_password is given (UniFi uses one).
    password: str = Field(default="", max_length=256)
    priv_password: str = Field(default="", max_length=256)
    community: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def _needs(self) -> ProfileIn:
        if self.version == "v2c":
            if not self.community:
                raise ValueError("SNMP v2c needs a community string")
            return self
        if not self.username:
            raise ValueError("SNMP v3 needs a username")
        if self.auth_protocol != "none" and len(self.password) < 8:
            raise ValueError("SNMP v3 passphrases must be at least 8 characters")
        if self.priv_protocol != "none" and self.auth_protocol == "none":
            raise ValueError("Privacy needs authentication")
        return self

    def secret(self) -> str:
        if self.version == "v2c":
            return json.dumps({"community": self.community})
        priv = self.priv_password or self.password
        return json.dumps({"auth_key": self.password, "priv_key": priv if self.priv_protocol != "none" else ""})


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Replacing secrets needs the full set again, so the stored JSON stays consistent.
    secrets: ProfileIn | None = None


async def _profile_out(db: AsyncSession, p: SnmpProfile) -> dict[str, Any]:
    cred = await db.get(Credential, p.credential_id) if p.credential_id else None
    targets = await db.scalar(select(func.count()).select_from(SnmpTarget).where(SnmpTarget.profile_id == p.id))
    return {
        "id": p.id,
        "name": p.name,
        "version": p.version,
        "username": p.username,
        "auth_protocol": p.auth_protocol,
        "priv_protocol": p.priv_protocol,
        "credential_fingerprint": cred.fingerprint if cred else None,
        "target_count": targets or 0,
        "is_demo": p.is_demo,
    }


def _apply_profile(p: SnmpProfile, body: ProfileIn) -> None:
    p.version, p.username = body.version, body.username if body.version == "v3" else ""
    p.auth_protocol, p.priv_protocol = body.auth_protocol, body.priv_protocol


@router.get("/profiles")
async def list_profiles(db: DB, principal: Reader) -> dict:
    return {"items": [await _profile_out(db, p) for p in await db.scalars(select(SnmpProfile).order_by(SnmpProfile.name))]}


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(body: ProfileIn, db: DB, state: State, principal: Manager) -> dict:
    p = SnmpProfile(name=body.name)
    _apply_profile(p, body)
    cred = await state.vault.store(db, kind=f"snmp-{body.version}", name=f"SNMP credential for {body.name}", secret=body.secret())
    p.credential_id = cred.id
    db.add(p)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "snmp.profile.create",
        target_type="snmp_profile",
        target_id=p.id,
        detail={"version": p.version, "name": p.name},
    )
    return await _profile_out(db, p)


async def _get_profile(db: AsyncSession, pid: str) -> SnmpProfile:
    p = await db.get(SnmpProfile, pid)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return p


@router.patch("/profiles/{pid}")
async def update_profile(pid: str, body: ProfilePatch, db: DB, state: State, principal: Manager) -> dict:
    p = await _get_profile(db, pid)
    detail: dict[str, Any] = {}
    if body.name:
        p.name = detail["name"] = body.name
    if body.secrets:
        _apply_profile(p, body.secrets)
        if p.credential_id and (cred := await db.get(Credential, p.credential_id)):
            await state.vault.rotate(db, cred, body.secrets.secret())
        else:
            cred = await state.vault.store(db, kind=f"snmp-{p.version}", name=f"SNMP credential for {p.name}", secret=body.secrets.secret())
            p.credential_id = cred.id
        detail |= {"version": p.version, "secret_rotated": True}
    await audit.record(db, principal.actor, "snmp.profile.update", target_type="snmp_profile", target_id=p.id, detail=detail)
    return await _profile_out(db, p)


@router.delete("/profiles/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(pid: str, db: DB, principal: Manager) -> None:
    p = await _get_profile(db, pid)
    if await db.scalar(select(func.count()).select_from(SnmpTarget).where(SnmpTarget.profile_id == p.id)):
        await audit.record(db, principal.actor, "snmp.profile.delete.denied", target_type="snmp_profile", target_id=p.id)
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "Remove the targets that use this profile first.")
    await audit.record(db, principal.actor, "snmp.profile.delete", target_type="snmp_profile", target_id=p.id, detail={"name": p.name})
    cred_id = p.credential_id
    await db.delete(p)
    await db.flush()
    if cred_id and (cred := await db.get(Credential, cred_id)):
        await db.delete(cred)


# -- targets ---------------------------------------------------------------------------


def _unsupported(model: str) -> bool:
    return any(k in model.lower() for k in NO_SNMP)


def _device_ref(d: Device | None) -> dict[str, Any] | None:
    return {"id": d.id, "name": d.name, "model": d.model, "ip_address": d.ip_address, "state": d.state} if d else None


async def _target_out(db: AsyncSession, t: SnmpTarget) -> dict[str, Any]:
    ifaces = list(await db.scalars(select(SnmpInterface).where(SnmpInterface.target_id == t.id, SnmpInterface.present.is_(True))))
    phys = [i for i in ifaces if i.port_idx is not None or is_physical(i.if_type, i.name or i.descr)]
    stale = t.last_poll_at is None or t.last_poll_at < utcnow() - STALE
    profile = await db.get(SnmpProfile, t.profile_id)
    return {
        "id": t.id,
        "name": t.name,
        "host": t.host,
        "port": t.port,
        "profile": {"id": profile.id, "name": profile.name, "version": profile.version} if profile else None,
        "device": _device_ref(await db.get(Device, t.device_id) if t.device_id else None),
        "enabled": t.enabled,
        "status": t.status,
        "status_detail": t.status_detail,
        "hint": HINTS.get(t.error_kind or "error", HINTS["error"]) if t.status in ("degraded", "unreachable") else None,
        "stale": stale and t.status == "ok",
        "sys_name": t.sys_name,
        "sys_descr": t.sys_descr,
        "uptime_s": t.sys_uptime_s,
        "last_poll_at": t.last_poll_at,
        "interfaces": len(phys),
        "interfaces_up": sum(1 for i in phys if i.oper_status == "up"),
        "with_errors": sum(1 for i in phys if (i.errors_per_min or 0) > 0),
        "rx_bps": sum(i.rx_bps or 0 for i in phys if i.oper_status == "up"),
        "tx_bps": sum(i.tx_bps or 0 for i in phys if i.oper_status == "up"),
        "is_demo": t.is_demo,
    }


class TargetIn(BaseModel):
    profile_id: str
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.:\-\[\]]+$")
    port: int = Field(default=161, ge=1, le=65535)


class TargetCreate(BaseModel):
    profile_id: str
    # Either devices from the inventory (their management IPs) or one manual host.
    device_ids: list[str] = Field(default_factory=list, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.:\-\[\]]+$")
    port: int = Field(default=161, ge=1, le=65535)

    @model_validator(mode="after")
    def _one(self) -> TargetCreate:
        if bool(self.device_ids) == bool(self.host):
            raise ValueError("Give either device_ids or a host")
        return self


class TargetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    profile_id: str | None = None


@router.get("/candidates")
async def candidates(db: DB, principal: Reader) -> dict:
    """Switches and gateways the controller reports, with whether they can be polled and whether they already are."""
    targeted = set(await db.scalars(select(SnmpTarget.device_id).where(SnmpTarget.device_id.is_not(None))))
    port_counts = dict(
        (await db.execute(select(Port.device_id, func.count()).group_by(Port.device_id))).all()  # type: ignore[arg-type]
    )
    out = []
    for d in await db.scalars(select(Device).where(Device.present.is_(True)).order_by(Device.name)):
        feats = set(d.features or [])
        if not ({"switching", "gateway"} & feats) or port_counts.get(d.id, 0) < 2:
            continue
        reason = None
        if _unsupported(d.model):
            reason = "Ubiquiti documents no SNMP support for USW Flex and Ultra switches."
        elif not d.ip_address:
            reason = "The controller reports no management IP."
        out.append(
            {**_device_ref(d), "ports": port_counts.get(d.id, 0), "targeted": d.id in targeted, "unavailable": reason, "is_demo": d.is_demo}
        )  # type: ignore[dict-item]
    return {"items": out}


@router.get("/targets")
async def list_targets(db: DB, principal: Reader) -> dict:
    return {"items": [await _target_out(db, t) for t in await db.scalars(select(SnmpTarget).order_by(SnmpTarget.name))]}


@router.post("/targets/test")
async def test_target(body: TargetIn, db: DB, state: State, principal: Manager) -> dict:
    profile = await _get_profile(db, body.profile_id)
    secret = await state.vault.reveal(db, profile.credential_id) if profile.credential_id else None
    tmp = SnmpTarget(profile_id=profile.id, name="test", host=body.host, port=body.port, is_demo=False)
    try:
        snap = await probe(db, state, tmp, profile, credentials(profile, secret))
    except SnmpError as e:
        return {"ok": False, "error_kind": e.kind, "error": str(e), "hint": HINTS.get(e.kind, HINTS["error"])}
    return {
        "ok": True,
        "sys_name": snap.sys_name,
        "sys_descr": snap.sys_descr,
        "interfaces": len(snap.interfaces),
        "physical": sum(1 for i in snap.interfaces if i.physical),
    }


@router.post("/targets", status_code=status.HTTP_201_CREATED)
async def create_targets(body: TargetCreate, db: DB, state: State, principal: Manager) -> dict:
    profile = await _get_profile(db, body.profile_id)
    new: list[SnmpTarget] = []
    if body.host:
        if profile.is_demo:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The demo profile only polls demo devices")
        device = await db.scalar(select(Device).where(Device.ip_address == body.host, Device.present.is_(True)))
        new.append(
            SnmpTarget(
                profile_id=profile.id,
                name=body.name or (device.name if device else body.host),
                host=body.host,
                port=body.port,
                device_id=device.id if device else None,
                is_demo=profile.is_demo,
            )
        )
    for did in body.device_ids:
        d = await db.get(Device, did)
        if d is None or not d.ip_address:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Device {did} is unknown or has no management IP")
        if await db.scalar(select(SnmpTarget.id).where(SnmpTarget.device_id == d.id)):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{d.name} is already polled")
        if d.is_demo != profile.is_demo:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Demo devices are polled only with the demo profile, and live ones never are"
            )
        new.append(SnmpTarget(profile_id=profile.id, name=d.name, host=d.ip_address, port=body.port, device_id=d.id, is_demo=d.is_demo))
    for t in new:
        db.add(t)
    await db.flush()
    for t in new:
        await audit.record(
            db,
            principal.actor,
            "snmp.target.create",
            target_type="snmp_target",
            target_id=t.id,
            detail={"host": t.host, "profile": profile.name},
        )
        await poll_target(db, state, t)
    return {"items": [await _target_out(db, t) for t in new]}


async def _get_target(db: AsyncSession, tid: str) -> SnmpTarget:
    t = await db.get(SnmpTarget, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    return t


@router.patch("/targets/{tid}")
async def update_target(tid: str, body: TargetPatch, db: DB, principal: Manager) -> dict:
    t = await _get_target(db, tid)
    fields = body.model_dump(exclude_unset=True)
    if "profile_id" in fields:
        await _get_profile(db, fields["profile_id"])
    for k, v in fields.items():
        setattr(t, k, v)
    await audit.record(db, principal.actor, "snmp.target.update", target_type="snmp_target", target_id=t.id, detail=fields)
    return await _target_out(db, t)


@router.delete("/targets/{tid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(tid: str, db: DB, principal: Manager) -> None:
    t = await _get_target(db, tid)
    await db.execute(delete(SnmpInterface).where(SnmpInterface.target_id == t.id))
    await audit.record(db, principal.actor, "snmp.target.delete", target_type="snmp_target", target_id=t.id, detail={"host": t.host})
    await db.delete(t)


@router.post("/targets/{tid}/poll")
async def poll_now(tid: str, db: DB, state: State, principal: Manager) -> dict:
    return await poll_target(db, state, await _get_target(db, tid))


# -- interfaces ------------------------------------------------------------------------


def _util(i: SnmpInterface) -> float | None:
    if not i.speed_mbps or i.oper_status != "up" or (i.rx_bps is None and i.tx_bps is None):
        return None
    return round(100 * max(i.rx_bps or 0, i.tx_bps or 0) / (i.speed_mbps * 1e6), 1)


def _iface(i: SnmpInterface, t: SnmpTarget | None = None, d: Device | None = None) -> dict[str, Any]:
    out = {
        "id": i.id,
        "if_index": i.if_index,
        "name": i.name or i.descr,
        "descr": i.descr,
        "alias": i.alias,
        "physical": is_physical(i.if_type, i.name or i.descr),
        "port_idx": i.port_idx,
        "mac_address": i.mac_address,
        "admin_up": i.admin_up,
        "oper_status": i.oper_status,
        "speed_mbps": i.speed_mbps,
        "rx_bps": i.rx_bps,
        "tx_bps": i.tx_bps,
        "utilisation_pct": _util(i),
        "errors_per_min": i.errors_per_min,
        "discards_per_min": i.discards_per_min,
        "counters": i.counters,
        "observed_at": i.observed_at,
        "present": i.present,
        "source": i.source,
        "is_demo": i.is_demo,
    }
    if t is not None:
        out["target"] = {"id": t.id, "name": t.name}
        out["device"] = _device_ref(d)
    return out


@router.get("/interfaces")
async def list_interfaces(
    db: DB,
    principal: Reader,
    target_id: str | None = None,
    q: str | None = Query(default=None, max_length=64),
    physical: bool = True,
    problems: bool = False,
) -> dict:
    stmt = select(SnmpInterface).where(SnmpInterface.present.is_(True))
    if target_id:
        stmt = stmt.where(SnmpInterface.target_id == target_id)
    targets = {t.id: t for t in await db.scalars(select(SnmpTarget))}
    devices = {
        d.id: d for d in await db.scalars(select(Device).where(Device.id.in_([t.device_id for t in targets.values() if t.device_id])))
    }
    items = []
    for i in await db.scalars(stmt.order_by(SnmpInterface.target_id, SnmpInterface.if_index)):
        t = targets.get(i.target_id)
        row = _iface(i, t, devices.get(t.device_id) if t and t.device_id else None)
        if physical and not row["physical"]:
            continue
        if problems and not ((i.errors_per_min or 0) > 0 or (i.discards_per_min or 0) > 0 or (row["utilisation_pct"] or 0) >= UTIL_WARN):
            continue
        if q and q.lower() not in " ".join([row["name"], i.alias, t.name if t else ""]).lower():
            continue
        items.append(row)
    return {"items": items}


@router.get("/devices/{device_id}")
async def device_interfaces(device_id: str, db: DB, principal: Reader) -> dict:
    """The SNMP view of one UniFi device, for the device page. `target` is null when it is not polled."""
    t = await db.scalar(select(SnmpTarget).where(SnmpTarget.device_id == device_id))
    if t is None:
        return {"target": None, "interfaces": []}
    ifaces = await db.scalars(
        select(SnmpInterface).where(SnmpInterface.target_id == t.id, SnmpInterface.present.is_(True)).order_by(SnmpInterface.if_index)
    )
    return {"target": await _target_out(db, t), "interfaces": [_iface(i) for i in ifaces]}


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    items: list[dict[str, Any]] = []
    targets = list(await db.scalars(select(SnmpTarget).where(SnmpTarget.enabled.is_(True))))
    for t in targets:
        if t.status in ("degraded", "unreachable"):
            items.append(
                {
                    "kind": "target_failing",
                    "severity": "warning",
                    "title": f"{t.name}: SNMP polling fails",
                    "detail": t.status_detail,
                    "target_id": t.id,
                    "is_demo": t.is_demo,
                }
            )
    by_id = {t.id: t for t in targets}
    macs: dict[str, set[str]] = {}
    for i in await db.scalars(select(SnmpInterface).where(SnmpInterface.present.is_(True), SnmpInterface.target_id.in_(list(by_id)))):
        t = by_id[i.target_id]
        if i.mac_address and is_physical(i.if_type, i.name or i.descr):
            macs.setdefault(t.id, set()).add(i.mac_address)
        name = f"{t.name} {i.name or i.descr}"
        base = {"target_id": t.id, "interface_id": i.id, "is_demo": i.is_demo}
        if (i.errors_per_min or 0) > 0:
            items.append(
                {
                    **base,
                    "kind": "errors",
                    "severity": "warning",
                    "title": f"{name} is logging {round(i.errors_per_min, 1):g} errors/min",
                    "detail": "Input or output errors usually mean a bad cable, a dirty or failing optic, or a duplex mismatch.",
                }
            )
        if (i.discards_per_min or 0) > 0:
            items.append(
                {
                    **base,
                    "kind": "discards",
                    "severity": "info",
                    "title": f"{name} is discarding {round(i.discards_per_min, 1):g} packets/min",
                    "detail": "Discards are packets dropped without an error, usually buffer exhaustion under bursts or congestion.",
                }
            )
        if (u := _util(i)) is not None and u >= UTIL_WARN:
            items.append(
                {
                    **base,
                    "kind": "saturated",
                    "severity": "warning",
                    "title": f"{name} is at {u:g}% of {i.speed_mbps} Mbit/s",
                    "detail": "Sustained high utilisation adds latency.",
                }
            )
    # The same agent reached at two addresses (e.g. a gateway's LAN and management IPs) double-counts its traffic.
    seen: dict[frozenset[str], SnmpTarget] = {}
    for tid, ms in macs.items():
        key = frozenset(ms)
        if len(key) < 2:
            continue
        if (first := seen.get(key)) is not None:
            t = by_id[tid]
            items.append(
                {
                    "kind": "duplicate_agent",
                    "severity": "info",
                    "title": f"{t.name} and {first.name} are the same agent",
                    "detail": (
                        "Both addresses answer with the same interfaces and MAC addresses. "
                        "Keep one target so its traffic is not counted twice."
                    ),
                    "target_id": t.id,
                    "is_demo": t.is_demo,
                }
            )
        else:
            seen[key] = by_id[tid]
    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda f: order[f["severity"]])
    return {"items": items}


# -- demo ------------------------------------------------------------------------------


@router.post("/demo", status_code=status.HTTP_201_CREATED)
async def create_demo(db: DB, state: State, principal: Manager) -> dict:
    if not state.settings.demo_controller_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo data is disabled on this installation.")
    if await db.scalar(select(SnmpProfile).where(SnmpProfile.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "The demo SNMP profile already exists.")
    devices = [d for d in (await candidates(db, principal))["items"] if d["is_demo"] and not d["unavailable"]]
    if not devices:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect the demo controller first; the demo targets are its switches and gateway.")
    body = ProfileIn(name="Demo SNMP v3", version="v3", username=SIM_USER, password=SIM_PASSWORD)
    p = SnmpProfile(name=body.name, is_demo=True)
    _apply_profile(p, body)
    p.credential_id = (await state.vault.store(db, kind="snmp-v3", name="demo SNMP credential", secret=body.secret())).id
    db.add(p)
    await db.flush()
    targets = [SnmpTarget(profile_id=p.id, name=d["name"], host=d["ip_address"], device_id=d["id"], is_demo=True) for d in devices]
    for t in targets:
        db.add(t)
    await db.flush()
    for t in targets:
        await poll_target(db, state, t)
    await audit.record(db, principal.actor, "snmp.demo.create", detail={"targets": len(targets)})
    return {"items": [await _target_out(db, t) for t in targets]}


@router.delete("/demo")
async def remove_demo(db: DB, principal: Manager) -> dict:
    targets = list(await db.scalars(select(SnmpTarget).where(SnmpTarget.is_demo.is_(True))))
    for t in targets:
        await db.execute(delete(SnmpInterface).where(SnmpInterface.target_id == t.id))
        await db.delete(t)
    profiles = list(await db.scalars(select(SnmpProfile).where(SnmpProfile.is_demo.is_(True))))
    await db.flush()
    for p in profiles:
        cred_id = p.credential_id
        await db.delete(p)
        await db.flush()
        if cred_id and (cred := await db.get(Credential, cred_id)):
            await db.delete(cred)
    await audit.record(db, principal.actor, "snmp.demo.remove", detail={"targets": len(targets)})
    return {"removed": len(targets)}

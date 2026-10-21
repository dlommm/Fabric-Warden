"""Servers & virtualization: host connections, hosts, workloads, storage and findings.

All integrations are read-only (see integrations/hosts/clients.py). Secrets
live in the credential vault; responses only ever show a fingerprint.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.integrations.hosts.clients import HostError
from warden.integrations.hosts.simulators import SIM_PVE_TOKEN, SIM_UNRAID_KEY
from warden.integrations.hosts.sync import collect, sync_connection
from warden.models import Client, Credential, Host, HostConnection, HostStorage, Workload
from warden.security.rbac import P

router = APIRouter(prefix="/api/servers", tags=["servers"], dependencies=[Depends(require_module("servers"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.SERVERS_MANAGE)
Editor = Allowed(P.INVENTORY_EDIT)

Kind = Literal["proxmox", "unraid", "docker", "node_exporter"]
STORAGE_WARN = 90.0
STORAGE_NOTE = 80.0
DISK_HOT_C = 50
STALE = timedelta(minutes=10)

HINTS = {
    "auth": (
        "Check the token. Proxmox needs USER@REALM!TOKENID plus the secret, with PVEAuditor "
        "(or VM.Audit, Sys.Audit, Datastore.Audit). Unraid needs an API key with read access."
    ),
    "unreachable": (
        "The host did not answer. Check the URL and port (Proxmox 8006, Unraid 80/443, Docker socket proxy 2375, "
        "node_exporter 9100) and the firewall between Fabric Warden and the host."
    ),
    "tls": (
        "The certificate could not be verified. Paste the host's CA certificate, "
        "or turn verification off for this connection on a trusted network."
    ),
    "not_found": (
        "The API answered but the endpoint is missing. Unraid needs 7.2 or later (or the Unraid Connect plugin); "
        "Docker needs Engine API 1.47 or later."
    ),
    "protocol": "The host answered with an error. See the detail.",
    "refused": "Fabric Warden only sends read requests.",
    "error": "The connection failed. See the detail.",
}


def _pct(used: int | None, total: int | None) -> float | None:
    return round(100 * used / total, 1) if used is not None and total else None


# -- connections ----------------------------------------------------------------------


class ConnIn(BaseModel):
    kind: Kind
    base_url: str = Field(min_length=8, max_length=512, pattern=r"^(https?://|unix:///)")
    token_id: str = Field(default="", max_length=128)
    secret: str = Field(default="", max_length=1024)
    verify_tls: bool = True
    ca_bundle_pem: str | None = Field(default=None, max_length=65536)

    @model_validator(mode="after")
    def _needs(self) -> ConnIn:
        if self.kind == "proxmox" and (not self.token_id or "!" not in self.token_id or not self.secret):
            raise ValueError("Proxmox needs a token id like monitor@pve!fabricwarden and its secret")
        if self.kind == "unraid" and not self.secret:
            raise ValueError("Unraid needs an API key")
        return self


class ConnCreate(ConnIn):
    name: str = Field(min_length=1, max_length=128)


class ConnPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    verify_tls: bool | None = None
    secret: str | None = Field(default=None, min_length=1, max_length=1024)


async def _conn_out(db: AsyncSession, c: HostConnection) -> dict[str, Any]:
    fp = None
    if c.credential_id:
        cred = await db.get(Credential, c.credential_id)
        fp = cred.fingerprint if cred else None
    host_ids = list(await db.scalars(select(Host.id).where(Host.connection_id == c.id, Host.present.is_(True))))
    workloads = (
        await db.scalar(select(func.count()).select_from(Workload).where(Workload.host_id.in_(host_ids), Workload.present.is_(True)))
        if host_ids
        else 0
    )
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "base_url": c.base_url,
        "token_id": c.token_id,
        "credential_fingerprint": fp,
        "verify_tls": c.verify_tls,
        "enabled": c.enabled,
        "status": c.status,
        "status_detail": c.status_detail,
        "version": c.version,
        "last_sync_at": c.last_sync_at,
        "host_count": len(host_ids),
        "workload_count": workloads or 0,
        "is_demo": c.is_demo,
    }


async def _probe(state: State, body: ConnIn) -> dict[str, Any]:
    tmp = HostConnection(
        name="test",
        kind=body.kind,
        base_url=body.base_url.rstrip("/"),
        token_id=body.token_id,
        verify_tls=body.verify_tls,
        ca_bundle_pem=body.ca_bundle_pem,
    )
    try:
        snap = await collect(state, tmp, body.secret or None)
    except HostError as e:
        return {"ok": False, "error_kind": e.kind, "error": str(e), "hint": HINTS.get(e.kind, HINTS["error"])}
    return {
        "ok": True,
        "version": snap.version,
        "hosts": [h.name for h in snap.hosts],
        "workloads": len(snap.workloads),
        "storage": len(snap.storage),
        "notes": snap.notes,
    }


@router.get("/connections")
async def list_connections(db: DB, principal: Reader) -> dict:
    return {"items": [await _conn_out(db, c) for c in await db.scalars(select(HostConnection).order_by(HostConnection.name))]}


@router.post("/connections/test")
async def test_connection(body: ConnIn, state: State, principal: Manager) -> dict:
    return await _probe(state, body)


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_connection(body: ConnCreate, db: DB, state: State, principal: Manager) -> dict:
    result = await _probe(state, body)
    if not result["ok"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result)
    c = HostConnection(
        name=body.name,
        kind=body.kind,
        base_url=body.base_url.rstrip("/"),
        token_id=body.token_id,
        verify_tls=body.verify_tls,
        ca_bundle_pem=body.ca_bundle_pem,
    )
    if body.secret:
        cred = await state.vault.store(db, kind=f"{body.kind}-secret", name=f"{body.kind} credential for {body.name}", secret=body.secret)
        c.credential_id = cred.id
    db.add(c)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "servers.connection.create",
        target_type="host_connection",
        target_id=c.id,
        detail={"kind": c.kind, "base_url": c.base_url},
    )
    await sync_connection(db, state, c)
    return await _conn_out(db, c)


async def _get_conn(db: AsyncSession, cid: str) -> HostConnection:
    c = await db.get(HostConnection, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    return c


@router.patch("/connections/{cid}")
async def update_connection(cid: str, body: ConnPatch, db: DB, state: State, principal: Manager) -> dict:
    c = await _get_conn(db, cid)
    fields = body.model_dump(exclude_unset=True, exclude={"secret"})
    for k, v in fields.items():
        setattr(c, k, v)
    if body.secret:
        if c.credential_id and (cred := await db.get(Credential, c.credential_id)):
            await state.vault.rotate(db, cred, body.secret)
        else:
            cred = await state.vault.store(db, kind=f"{c.kind}-secret", name=f"{c.kind} credential for {c.name}", secret=body.secret)
            c.credential_id = cred.id
        fields["secret_rotated"] = True
    await audit.record(db, principal.actor, "servers.connection.update", target_type="host_connection", target_id=c.id, detail=fields)
    return await _conn_out(db, c)


@router.delete("/connections/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(cid: str, db: DB, principal: Manager) -> None:
    c = await _get_conn(db, cid)
    await _purge(db, [c.id])
    await audit.record(
        db, principal.actor, "servers.connection.delete", target_type="host_connection", target_id=c.id, detail={"name": c.name}
    )
    await db.delete(c)


async def _purge(db: AsyncSession, conn_ids: list[str]) -> None:
    host_ids = select(Host.id).where(Host.connection_id.in_(conn_ids))
    await db.execute(delete(Workload).where(Workload.host_id.in_(host_ids)))
    await db.execute(delete(HostStorage).where(HostStorage.host_id.in_(host_ids)))
    await db.execute(delete(Host).where(Host.connection_id.in_(conn_ids)))


@router.post("/connections/{cid}/sync")
async def sync_now(cid: str, db: DB, state: State, principal: Manager) -> dict:
    c = await _get_conn(db, cid)
    return await sync_connection(db, state, c)


DEMO = [
    ("pve-lab (Proxmox)", "proxmox", "https://proxmox-simulator.local:8006", "demo@pve!fabricwarden", SIM_PVE_TOKEN.split("=", 2)[2]),
    ("unraid-tower", "unraid", "http://unraid-simulator.local", "", SIM_UNRAID_KEY),
    ("docker-host", "docker", "http://docker-simulator.local:2375", "", ""),
    ("nas-backup (node_exporter)", "node_exporter", "http://node-exporter-simulator.local:9100", "", ""),
]


@router.post("/demo", status_code=status.HTTP_201_CREATED)
async def create_demo(db: DB, state: State, principal: Manager) -> dict:
    if not state.settings.demo_controller_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo data is disabled on this installation.")
    if await db.scalar(select(HostConnection).where(HostConnection.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "The demo servers already exist.")
    out = []
    for name, kind, url, token_id, secret in DEMO:
        c = HostConnection(name=name, kind=kind, base_url=url, token_id=token_id, is_demo=True)
        if secret:
            cred = await state.vault.store(db, kind=f"{kind}-secret", name=f"demo {kind} credential", secret=secret)
            c.credential_id = cred.id
        db.add(c)
        await db.flush()
        await sync_connection(db, state, c)
        out.append(await _conn_out(db, c))
    await audit.record(db, principal.actor, "servers.demo.create", detail={"connections": len(out)})
    return {"items": out}


@router.delete("/demo")
async def remove_demo(db: DB, principal: Manager) -> dict:
    conns = list(await db.scalars(select(HostConnection).where(HostConnection.is_demo.is_(True))))
    await _purge(db, [c.id for c in conns])
    for c in conns:
        await db.delete(c)
    await audit.record(db, principal.actor, "servers.demo.remove", detail={"connections": len(conns)})
    return {"removed": len(conns)}


# -- hosts and workloads --------------------------------------------------------------


def _client_ref(c: Client | None, reason: str | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "id": c.id,
        "name": c.alias or c.name,
        "ip_address": c.ip_address,
        "mac_address": c.mac_address,
        "present": c.present,
        "match": reason,
    }


def _host(h: Host, conn: HostConnection | None, clients: dict[str, Client], counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    stale = h.observed_at < utcnow() - STALE
    return {
        "id": h.id,
        "name": h.name,
        "kind": h.kind,
        "status": "unknown" if stale and h.status == "online" else h.status,
        "hostname": h.hostname,
        "os": h.os,
        "version": h.version,
        "ip_address": h.ip_address,
        "mac_addresses": h.mac_addresses or [],
        "cpu_count": h.cpu_count,
        "cpu_pct": h.cpu_pct,
        "mem_total": h.mem_total,
        "mem_used": h.mem_used,
        "mem_pct": _pct(h.mem_used, h.mem_total),
        "load_1m": h.load_1m,
        "uptime_s": h.uptime_s,
        "extra": {k: v for k, v in (h.extra or {}).items() if k not in ("cpu_idle", "cpu_total")},
        "client": _client_ref(clients.get(h.client_id or ""), h.match_reason),
        "connection": {"id": conn.id, "name": conn.name, "kind": conn.kind, "status": conn.status} if conn else None,
        "workloads": counts.get(h.id, {"total": 0, "running": 0}),
        "labels": h.labels or [],
        "notes": h.notes,
        "provenance": {
            "source": h.source,
            "observed_at": h.observed_at,
            "first_seen_at": h.first_seen_at,
            "present": h.present,
            "is_demo": h.is_demo,
            "age_seconds": int((utcnow() - h.observed_at).total_seconds()),
        },
        "is_demo": h.is_demo,
    }


def _workload(w: Workload, host: Host | None, clients: dict[str, Client]) -> dict[str, Any]:
    return {
        "id": w.id,
        "host_id": w.host_id,
        "host": host.name if host else None,
        "host_kind": host.kind if host else None,
        "kind": w.kind,
        "name": w.name,
        "status": w.status,
        "raw_status": w.raw_status,
        "vmid": w.vmid,
        "cpu_pct": w.cpu_pct,
        "cpu_count": w.cpu_count,
        "mem_used": w.mem_used,
        "mem_max": w.mem_max,
        "mem_pct": _pct(w.mem_used, w.mem_max),
        "disk_max": w.disk_max,
        "uptime_s": w.uptime_s,
        "ip_addresses": w.ip_addresses or [],
        "mac_addresses": w.mac_addresses or [],
        "image": w.image,
        "ports": w.ports or [],
        "autostart": w.autostart,
        "template": w.template,
        "tags": w.tags or [],
        "client": _client_ref(clients.get(w.client_id or ""), w.match_reason),
        "present": w.present,
        "is_demo": w.is_demo,
    }


def _storage(s: HostStorage) -> dict[str, Any]:
    return {
        "id": s.id,
        "host_id": s.host_id,
        "name": s.name,
        "kind": s.kind,
        "total_bytes": s.total_bytes,
        "used_bytes": s.used_bytes,
        "used_pct": _pct(s.used_bytes, s.total_bytes),
        "status": s.status,
        "healthy": s.healthy,
        "shared": s.shared,
        "temperature_c": s.temperature_c,
        "extra": s.extra or {},
        "is_demo": s.is_demo,
    }


async def _clients(db: AsyncSession, ids: list[str | None]) -> dict[str, Client]:
    wanted = [i for i in ids if i]
    return {c.id: c for c in await db.scalars(select(Client).where(Client.id.in_(wanted)))} if wanted else {}


async def _counts(db: AsyncSession) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for w in await db.scalars(select(Workload).where(Workload.present.is_(True), Workload.template.is_(False))):
        c = out.setdefault(w.host_id, {"total": 0, "running": 0})
        c["total"] += 1
        c["running"] += w.status == "running"
    return out


@router.get("/hosts")
async def list_hosts(db: DB, principal: Reader) -> dict:
    hosts = list(await db.scalars(select(Host).where(Host.present.is_(True)).order_by(Host.name)))
    conns = {c.id: c for c in await db.scalars(select(HostConnection))}
    clients = await _clients(db, [h.client_id for h in hosts])
    counts = await _counts(db)
    storage: dict[str, list[HostStorage]] = {}
    for s in await db.scalars(select(HostStorage).where(HostStorage.present.is_(True))):
        storage.setdefault(s.host_id, []).append(s)
    items = []
    for h in hosts:
        row = _host(h, conns.get(h.connection_id), clients, counts)
        pools = [s for s in storage.get(h.id, []) if s.kind in ("datastore", "array", "filesystem") and s.total_bytes]
        worst = max(pools, key=lambda s: _pct(s.used_bytes, s.total_bytes) or 0, default=None)
        row["storage_worst"] = {"name": worst.name, "used_pct": _pct(worst.used_bytes, worst.total_bytes)} if worst else None
        row["storage_unhealthy"] = sum(1 for s in storage.get(h.id, []) if s.healthy is False)
        items.append(row)
    return {"items": items}


@router.get("/hosts/{host_id}")
async def get_host(host_id: str, db: DB, principal: Reader) -> dict:
    h = await db.get(Host, host_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found")
    workloads = list(await db.scalars(select(Workload).where(Workload.host_id == h.id, Workload.present.is_(True)).order_by(Workload.name)))
    storage = list(
        await db.scalars(
            select(HostStorage)
            .where(HostStorage.host_id == h.id, HostStorage.present.is_(True))
            .order_by(HostStorage.kind, HostStorage.name)
        )
    )
    clients = await _clients(db, [h.client_id, *[w.client_id for w in workloads]])
    conn = await db.get(HostConnection, h.connection_id)
    return {
        **_host(h, conn, clients, await _counts(db)),
        "workload_items": [_workload(w, h, clients) for w in workloads],
        "storage_items": [_storage(s) for s in storage],
    }


class HostPatch(BaseModel):
    labels: list[str] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=20_000)


@router.patch("/hosts/{host_id}")
async def update_host(host_id: str, body: HostPatch, db: DB, principal: Editor) -> dict:
    h = await db.get(Host, host_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found")
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(h, k, v)
    await audit.record(db, principal.actor, "servers.host.update", target_type="host", target_id=h.id, detail=fields)
    return {"id": h.id, "labels": h.labels, "notes": h.notes}


@router.get("/workloads")
async def list_workloads(
    db: DB,
    principal: Reader,
    q: str | None = None,
    kind: Literal["vm", "lxc", "container"] | None = None,
    status_: Literal["running", "stopped", "paused", "other"] | None = Query(None, alias="status"),
    host_id: str | None = None,
) -> dict:
    stmt = select(Workload).where(Workload.present.is_(True)).order_by(Workload.name)
    if kind:
        stmt = stmt.where(Workload.kind == kind)
    if status_:
        stmt = stmt.where(Workload.status == status_)
    if host_id:
        stmt = stmt.where(Workload.host_id == host_id)
    rows = list(await db.scalars(stmt))
    if q:
        t = q.lower()
        rows = [
            w
            for w in rows
            if t in w.name.lower() or t in (w.image or "").lower() or any(t in ip for ip in w.ip_addresses or []) or t == str(w.vmid)
        ]
    hosts = {h.id: h for h in await db.scalars(select(Host))}
    clients = await _clients(db, [w.client_id for w in rows])
    return {"items": [_workload(w, hosts.get(w.host_id), clients) for w in rows]}


@router.get("/clients/{client_id}")
async def runs_on(client_id: str, db: DB, principal: Reader) -> dict:
    """What a network client is on the server side: a host, or a VM/container on a host."""
    host = await db.scalar(select(Host).where(Host.client_id == client_id, Host.present.is_(True)))
    workload = await db.scalar(select(Workload).where(Workload.client_id == client_id, Workload.present.is_(True)))
    out: dict[str, Any] = {"host": None, "workload": None}
    if host:
        out["host"] = {
            "id": host.id,
            "name": host.name,
            "kind": host.kind,
            "status": host.status,
            "match": host.match_reason,
            "is_demo": host.is_demo,
        }
    if workload:
        wh = await db.get(Host, workload.host_id)
        out["workload"] = {
            "id": workload.id,
            "name": workload.name,
            "kind": workload.kind,
            "status": workload.status,
            "vmid": workload.vmid,
            "match": workload.match_reason,
            "host": {"id": wh.id, "name": wh.name, "kind": wh.kind} if wh else None,
            "is_demo": workload.is_demo,
        }
    return out


# -- findings -------------------------------------------------------------------------


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    out: list[dict[str, Any]] = []
    for c in await db.scalars(select(HostConnection).where(HostConnection.enabled.is_(True))):
        if c.status in ("unreachable", "degraded"):
            out.append(
                {
                    "key": f"conn:{c.id}",
                    "kind": "connection",
                    "severity": "critical" if c.status == "unreachable" else "warning",
                    "title": f"{c.name}: {c.status_detail or c.status}",
                    "connection_id": c.id,
                }
            )
    hosts = {h.id: h for h in await db.scalars(select(Host).where(Host.present.is_(True)))}
    for h in hosts.values():
        if h.status == "offline":
            out.append(
                {
                    "key": f"host_offline:{h.id}",
                    "kind": "host_offline",
                    "severity": "critical",
                    "title": f"{h.name} is offline",
                    "host_id": h.id,
                }
            )
        for u in (h.extra or {}).get("ups") or []:
            if u.get("status") and u["status"] != "Online":
                out.append(
                    {
                        "key": f"ups:{h.id}:{u.get('name')}",
                        "kind": "ups",
                        "severity": "warning",
                        "title": f"{h.name}: UPS {u.get('model') or u.get('name')} reports {u['status']}",
                        "host_id": h.id,
                    }
                )
            if u.get("battery_health") == "Replace":
                out.append(
                    {
                        "key": f"ups_battery:{h.id}",
                        "kind": "ups",
                        "severity": "warning",
                        "title": f"{h.name}: UPS battery needs replacing",
                        "host_id": h.id,
                    }
                )
        if h.extra.get("array_state") and h.extra["array_state"] != "STARTED":
            out.append(
                {
                    "key": f"array:{h.id}",
                    "kind": "array",
                    "severity": "critical",
                    "title": f"{h.name}: array is {h.extra['array_state'].lower().replace('_', ' ')}",
                    "host_id": h.id,
                }
            )
        if h.client_id is None and h.status == "online":
            out.append(
                {
                    "key": f"host_unseen:{h.id}",
                    "kind": "not_on_network",
                    "severity": "info",
                    "title": f"{h.name} is not matched to any client the network reports",
                    "detail": "No client has its address, MAC or hostname.",
                    "host_id": h.id,
                }
            )
    for w in await db.scalars(select(Workload).where(Workload.present.is_(True), Workload.template.is_(False))):
        h = hosts.get(w.host_id)
        if h is None or h.status != "online":
            continue
        label = {"vm": "VM", "lxc": "LXC", "container": "Container"}[w.kind]
        if w.autostart and w.status != "running":
            out.append(
                {
                    "key": f"autostart:{w.id}",
                    "kind": "autostart_stopped",
                    "severity": "warning",
                    "title": f"{label} {w.name} on {h.name} starts automatically but is not running",
                    "detail": w.raw_status if w.raw_status.lower() != w.status else "",
                    "host_id": h.id,
                    "workload_id": w.id,
                }
            )
        if w.kind in ("vm", "lxc") and w.status == "running" and w.ip_addresses and w.client_id is None:
            out.append(
                {
                    "key": f"workload_unseen:{w.id}",
                    "kind": "not_on_network",
                    "severity": "info",
                    "title": f"{label} {w.name} ({', '.join(w.ip_addresses)}) is not seen by the network",
                    "detail": "It may be on an isolated bridge, or the controller has not seen it yet.",
                    "host_id": h.id,
                    "workload_id": w.id,
                }
            )
    shared_seen: set[str] = set()
    for s in await db.scalars(select(HostStorage).where(HostStorage.present.is_(True))):
        h = hosts.get(s.host_id)
        if h is None:
            continue
        pct = _pct(s.used_bytes, s.total_bytes)
        if s.kind == "disk":
            if s.healthy is False:
                out.append(
                    {
                        "key": f"disk:{s.id}",
                        "kind": "disk",
                        "severity": "critical",
                        "title": f"{h.name}: {s.name} is {s.status.removeprefix('DISK_').lower().replace('_', ' ')}",
                        "host_id": h.id,
                    }
                )
            elif s.temperature_c is not None and s.temperature_c >= DISK_HOT_C:
                out.append(
                    {
                        "key": f"disk_hot:{s.id}",
                        "kind": "disk_hot",
                        "severity": "warning",
                        "title": f"{h.name}: {s.name} is at {s.temperature_c:.0f} °C",
                        "host_id": h.id,
                    }
                )
        elif pct is not None and pct >= STORAGE_NOTE:
            # A shared datastore is one finding, not one per node that mounts it.
            if s.shared:
                if s.name in shared_seen:
                    continue
                shared_seen.add(s.name)
            where = f"Shared storage {s.name}" if s.shared else f"{h.name}: {s.name}"
            sev = "warning" if pct >= STORAGE_WARN else "info"
            out.append(
                {
                    "key": f"storage:{s.name if s.shared else s.id}",
                    "kind": "storage_full",
                    "severity": sev,
                    "title": f"{where} is {pct}% full",
                    "host_id": h.id,
                }
            )
    order = {"critical": 0, "warning": 1, "info": 2}
    return {"items": sorted(out, key=lambda f: (order[f["severity"]], f["title"]))}

"""Collect from host connections, upsert observed rows and match them to network clients."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import metrics
from warden.db import utcnow
from warden.integrations.hosts.clients import (
    HostError,
    ReadOnlyHTTP,
    Snapshot,
    collect_docker,
    collect_node_exporter,
    collect_proxmox,
    collect_unraid,
)
from warden.models import Client, Host, HostConnection, HostStorage, Workload

if TYPE_CHECKING:
    from warden.app_state import AppState
    from warden.jobs import JobContext

log = logging.getLogger(__name__)

KINDS = ("proxmox", "unraid", "docker", "node_exporter")
SOURCE_NAME = {"proxmox": "proxmox-ve-api", "unraid": "unraid-api", "docker": "docker-engine-api", "node_exporter": "node-exporter"}


def headers_for(conn: HostConnection, secret: str | None) -> dict[str, str]:
    if conn.kind == "proxmox" and secret:
        return {"Authorization": f"PVEAPIToken={conn.token_id}={secret}"}
    if conn.kind == "unraid" and secret:
        return {"x-api-key": secret}
    if conn.kind in ("docker", "node_exporter") and secret:
        # Optional bearer token for a reverse proxy in front of the socket proxy or exporter.
        return {"Authorization": f"Bearer {secret}"}
    return {}


async def collect(state: AppState, conn: HostConnection, secret: str | None, previous: dict[str, Any] | None = None) -> Snapshot:
    async with ReadOnlyHTTP(
        conn.base_url,
        headers=headers_for(conn, secret),
        verify_tls=conn.verify_tls,
        ca_bundle_pem=conn.ca_bundle_pem,
        transport=state.host_transport(conn),
    ) as api:
        if conn.kind == "proxmox":
            return await collect_proxmox(api)
        if conn.kind == "unraid":
            return await collect_unraid(api, conn.base_url)
        if conn.kind == "docker":
            return await collect_docker(api, conn.base_url)
        if conn.kind == "node_exporter":
            return await collect_node_exporter(api, conn.base_url, previous)
    raise HostError(f"Unknown connection kind {conn.kind}", kind="error")


class ClientIndex:
    """Present UniFi clients by IP, MAC and name, for matching hosts and workloads."""

    def __init__(self, clients: list[Client]):
        self.by_ip = {c.ip_address: c for c in clients if c.ip_address}
        self.by_mac = {c.mac_address.lower(): c for c in clients if c.mac_address}
        self.by_name: dict[str, Client] = {}
        for c in clients:
            for n in (c.name, c.alias):
                if n:
                    self.by_name.setdefault(n.lower(), c)

    def match(self, ips: list[str], macs: list[str], names: list[str]) -> tuple[Client | None, str | None]:
        for ip in ips:
            if ip in self.by_ip:
                return self.by_ip[ip], "ip"
        for mac in macs:
            if mac.lower() in self.by_mac:
                return self.by_mac[mac.lower()], "mac"
        for n in names:
            if n and n.lower() in self.by_name:
                return self.by_name[n.lower()], "hostname"
        return None, None


def _apply(row: Any, values: dict[str, Any]) -> None:
    for k, v in values.items():
        setattr(row, k, v)


async def metrics_wanted(db: AsyncSession, state: AppState) -> bool:
    enabled = await state.modules.enabled_ids(db)
    return any(m in enabled for m in metrics.CONSUMERS)


async def sync_connection(
    db: AsyncSession, state: AppState, conn: HostConnection, *, collect_metrics: bool | None = None
) -> dict[str, Any]:
    secret = await state.vault.reveal(db, conn.credential_id) if conn.credential_id else None
    hosts_before = {h.external_id: h for h in await db.scalars(select(Host).where(Host.connection_id == conn.id))}
    previous = next((h.extra for h in hosts_before.values()), None) if conn.kind == "node_exporter" else None
    now = utcnow()
    try:
        snap = await collect(state, conn, secret, previous)
    except HostError as e:
        conn.status = "unreachable" if e.kind in ("unreachable", "tls") else "degraded"
        conn.status_detail = str(e)[:500]
        for h in hosts_before.values():
            h.status = "unknown"
        return {"connection": conn.id, "status": conn.status, "error": str(e)}

    source = f"{'simulator' if conn.is_demo else SOURCE_NAME[conn.kind]}@{snap.version or 'unknown'}"
    common = {"source": source, "is_demo": conn.is_demo, "observed_at": now, "present": True}
    index = ClientIndex(list(await db.scalars(select(Client).where(Client.present.is_(True)))))

    hosts: dict[str, Host] = {}
    for hd in snap.hosts:
        h = hosts_before.get(hd.external_id)
        if h is None:
            h = Host(connection_id=conn.id, external_id=hd.external_id, first_seen_at=now, labels=[], notes="")
            db.add(h)
        client, reason = index.match([hd.ip_address] if hd.ip_address else [], hd.mac_addresses, [hd.hostname, hd.name])
        _apply(
            h,
            {
                **common,
                "name": hd.name,
                "kind": hd.kind,
                "status": hd.status,
                "hostname": hd.hostname,
                "os": hd.os,
                "version": hd.version,
                "ip_address": hd.ip_address or (client.ip_address if client else None),
                "mac_addresses": hd.mac_addresses,
                "cpu_count": hd.cpu_count,
                "cpu_pct": hd.cpu_pct,
                "mem_total": hd.mem_total,
                "mem_used": hd.mem_used,
                "load_1m": hd.load_1m,
                "uptime_s": hd.uptime_s,
                "extra": hd.extra,
                "raw": hd.raw,
                "client_id": client.id if client else None,
                "match_reason": reason,
            },
        )
        hosts[hd.external_id] = h
    for ext, h in hosts_before.items():
        if ext not in hosts:
            h.present, h.status = False, "unknown"
    await db.flush()

    host_ids = [h.id for h in hosts.values()]
    seen_w: set[tuple[str, str]] = set()
    existing_w = {(w.host_id, w.external_id): w for w in await db.scalars(select(Workload).where(Workload.host_id.in_(host_ids)))}
    for wd in snap.workloads:
        h = hosts.get(wd.host)
        if h is None:
            continue
        key = (h.id, wd.external_id)
        seen_w.add(key)
        w = existing_w.get(key)
        if w is None:
            w = Workload(host_id=h.id, external_id=wd.external_id, first_seen_at=now)
            db.add(w)
        # Containers share the host's address, so only their own (macvlan) IP or MAC can match a client, never the name.
        names = [wd.name] if wd.kind in ("vm", "lxc") else []
        client, reason = index.match(wd.ip_addresses, wd.mac_addresses, names) if not wd.template else (None, None)
        _apply(
            w,
            {
                **common,
                "kind": wd.kind,
                "name": wd.name,
                "status": wd.status,
                "raw_status": wd.raw_status,
                "vmid": wd.vmid,
                "cpu_pct": wd.cpu_pct,
                "cpu_count": wd.cpu_count,
                "mem_used": wd.mem_used,
                "mem_max": wd.mem_max,
                "disk_max": wd.disk_max,
                "uptime_s": wd.uptime_s,
                "ip_addresses": wd.ip_addresses,
                "mac_addresses": wd.mac_addresses,
                "image": wd.image,
                "ports": wd.ports,
                "autostart": wd.autostart,
                "template": wd.template,
                "tags": wd.tags,
                "raw": wd.raw,
                "client_id": client.id if client else None,
                "match_reason": reason,
            },
        )
    for key, w in existing_w.items():
        if key not in seen_w:
            w.present = False

    seen_s: set[tuple[str, str]] = set()
    existing_s = {(s.host_id, s.external_id): s for s in await db.scalars(select(HostStorage).where(HostStorage.host_id.in_(host_ids)))}
    for sd in snap.storage:
        h = hosts.get(sd.host)
        if h is None:
            continue
        key = (h.id, sd.external_id)
        seen_s.add(key)
        s = existing_s.get(key)
        if s is None:
            s = HostStorage(host_id=h.id, external_id=sd.external_id, first_seen_at=now)
            db.add(s)
        _apply(
            s,
            {
                **common,
                "raw": {},
                "name": sd.name,
                "kind": sd.kind,
                "total_bytes": sd.total_bytes,
                "used_bytes": sd.used_bytes,
                "status": sd.status,
                "healthy": sd.healthy,
                "shared": sd.shared,
                "temperature_c": sd.temperature_c,
                "extra": sd.extra,
            },
        )
    for key, s in existing_s.items():
        if key not in seen_s:
            s.present = False

    conn.status = "ok" if not snap.notes else "degraded"
    conn.status_detail = "; ".join(snap.notes)[:500]
    conn.version = snap.version or conn.version
    conn.last_sync_at = now
    await db.flush()
    if collect_metrics is None:
        collect_metrics = await metrics_wanted(db, state)
    if collect_metrics:
        await metrics.record(db, host_samples(list(hosts.values()), conn.is_demo), now)
    return {"connection": conn.id, "status": conn.status, "hosts": len(hosts), "workloads": len(seen_w), "storage": len(seen_s)}


def host_samples(hosts: list[Host], demo: bool) -> list[metrics.Sample]:
    out: list[metrics.Sample] = []
    for h in hosts:
        if h.status != "online":
            continue
        if h.cpu_pct is not None:
            out.append(metrics.Sample("host.cpu_pct", "host", h.id, h.name, h.cpu_pct, None, demo))
        if h.mem_total and h.mem_used is not None:
            out.append(metrics.Sample("host.mem_pct", "host", h.id, h.name, round(100 * h.mem_used / h.mem_total, 1), None, demo))
        if h.load_1m is not None:
            out.append(metrics.Sample("host.load_1m", "host", h.id, h.name, h.load_1m, None, demo))
    return out


async def sync_job(ctx: JobContext) -> dict[str, Any]:
    collect_metrics = await metrics_wanted(ctx.db, ctx.state)
    q = select(HostConnection).where(HostConnection.enabled.is_(True))
    if cid := ctx.params.get("connection_id"):
        q = select(HostConnection).where(HostConnection.id == cid)
    results = []
    for conn in await ctx.db.scalars(q):
        results.append(await sync_connection(ctx.db, ctx.state, conn, collect_metrics=collect_metrics))
    return {"connections": results}

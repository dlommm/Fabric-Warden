"""Controller polling: capability detection and read-only inventory sync."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import events as ev
from warden.app_state import AppState
from warden.db import Base, utcnow
from warden.events import Event
from warden.integrations.unifi import capabilities
from warden.integrations.unifi.client import UniFiError, UniFiNetworkClient
from warden.jobs import JobContext
from warden.models import (
    Client,
    ClientSighting,
    Controller,
    Device,
    FirewallPolicy,
    FirewallZone,
    Network,
    Port,
    Radio,
    Site,
    Wan,
)

log = logging.getLogger(__name__)
M = TypeVar("M", bound=Base)


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SiteSync:
    """Upserts one site's objects and tracks which ones disappeared."""

    def __init__(
        self, db: AsyncSession, state: AppState, controller: Controller, site: Site, source: str, *, client_history: bool = False
    ) -> None:
        self.db = db
        self.client_history = client_history
        self._sightings: dict[str, ClientSighting] | None = None
        self.state = state
        self.controller = controller
        self.site = site
        self.source = source
        self.now = utcnow()
        self.counts: dict[str, int] = {}

    async def publish(self, type_: str, entity_type: str, entity_id: str, payload: dict[str, Any], severity: str = "info") -> None:
        await self.state.bus.publish(
            self.db,
            Event(
                type=type_,
                source=self.source,
                site_id=self.site.id,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                severity=severity,
            ),
        )

    async def upsert(
        self, model: type[M], items: list[dict[str, Any]], apply: Callable[[M, dict[str, Any], bool], Awaitable[None]]
    ) -> None:
        existing = {r.external_id: r for r in await self.db.scalars(select(model).where(model.site_id == self.site.id))}  # type: ignore[attr-defined]
        seen: set[str] = set()
        for item in items:
            ext = item["id"]
            seen.add(ext)
            row = existing.get(ext)
            created = row is None
            if row is None:
                row = model(site_id=self.site.id, external_id=ext, first_seen_at=self.now)  # type: ignore[call-arg]
                self.db.add(row)
            row.source = self.source  # type: ignore[attr-defined]
            row.is_demo = self.controller.is_demo  # type: ignore[attr-defined]
            row.observed_at = self.now  # type: ignore[attr-defined]
            row.raw = item  # type: ignore[attr-defined]
            was_present = created or row.present  # type: ignore[attr-defined]
            row.present = True  # type: ignore[attr-defined]
            await apply(row, item, created or not was_present)
        await self.db.flush()
        for ext, row in existing.items():
            if ext not in seen and row.present:  # type: ignore[attr-defined]
                row.present = False  # type: ignore[attr-defined]
                await self.on_missing(row)
        self.counts[model.__tablename__] = len(items)

    async def on_missing(self, row: Base) -> None:
        if isinstance(row, Device):
            await self.publish(ev.DEVICE_MISSING, "device", row.id, {"name": row.name, "mac": row.mac_address}, "warning")
        elif isinstance(row, Client):
            await self.publish(ev.CLIENT_DISCONNECTED, "client", row.id, {"name": row.name, "ip": row.ip_address})

    # -- per-type appliers -------------------------------------------------

    async def apply_device(self, d: Device, item: dict[str, Any], created: bool) -> None:
        prev_state, prev_fw = d.state, d.firmware_version
        d.name = item.get("name") or item.get("macAddress", "")
        d.model = item.get("model", "")
        d.mac_address = item.get("macAddress", "").lower()
        d.ip_address = item.get("ipAddress")
        d.firmware_version = item.get("firmwareVersion")
        d.firmware_updatable = bool(item.get("firmwareUpdatable"))
        d.state = item.get("state", "UNKNOWN")
        d.features = sorted(item.get("features") or [])
        d.uplink_device_external_id = (item.get("uplink") or {}).get("deviceId")
        if d.labels is None:
            d.labels = []
        await self.db.flush()
        await self._sync_interfaces(d, item.get("interfaces") or {})
        if created:
            await self.publish(ev.DEVICE_DISCOVERED, "device", d.id, {"name": d.name, "model": d.model, "state": d.state})
        else:
            if prev_state != d.state:
                sev = "info" if d.state == "ONLINE" else "warning"
                await self.publish(ev.DEVICE_STATE_CHANGED, "device", d.id, {"name": d.name, "from": prev_state, "to": d.state}, sev)
            if prev_fw and prev_fw != d.firmware_version:
                await self.publish(ev.DEVICE_FIRMWARE_CHANGED, "device", d.id, {"name": d.name, "from": prev_fw, "to": d.firmware_version})

    async def _sync_interfaces(self, d: Device, interfaces: dict[str, Any]) -> None:
        ports = {p.idx: p for p in await self.db.scalars(select(Port).where(Port.device_id == d.id))}
        for item in interfaces.get("ports") or []:
            p = ports.pop(item["idx"], None) or Port(device_id=d.id, idx=item["idx"])
            p.connector = item.get("connector", "")
            p.state = item.get("state", "UNKNOWN")
            p.speed_mbps = item.get("speedMbps")
            p.max_speed_mbps = item.get("maxSpeedMbps", 0)
            p.poe = item.get("poe")
            p.observed_at = self.now
            self.db.add(p)
        for stale in ports.values():
            await self.db.delete(stale)
        radios = {r.frequency_ghz: r for r in await self.db.scalars(select(Radio).where(Radio.device_id == d.id))}
        for item in interfaces.get("radios") or []:
            r = radios.pop(item["frequencyGHz"], None) or Radio(device_id=d.id, frequency_ghz=item["frequencyGHz"])
            r.channel = item.get("channel")
            r.channel_width_mhz = item.get("channelWidthMHz", 0)
            r.wlan_standard = item.get("wlanStandard", "")
            r.observed_at = self.now
            self.db.add(r)
        for stale in radios.values():
            await self.db.delete(stale)

    async def apply_client(self, c: Client, item: dict[str, Any], created: bool) -> None:
        prev = (c.ip_address, c.uplink_device_external_id)
        c.name = item.get("name") or item.get("macAddress") or item["id"]
        c.type = item.get("type", "UNKNOWN")
        c.mac_address = (item.get("macAddress") or "").lower() or None
        c.ip_address = item.get("ipAddress")
        c.uplink_device_external_id = item.get("uplinkDeviceId")
        c.connected_at = _ts(item.get("connectedAt"))
        c.access = item.get("access") or {}
        if c.labels is None:
            c.labels = []
        if created:
            await self.db.flush()
            await self.publish(ev.CLIENT_CONNECTED, "client", c.id, {"name": c.name, "ip": c.ip_address, "type": c.type})
        elif prev != (c.ip_address, c.uplink_device_external_id):
            payload = {
                "name": c.name,
                "from_ip": prev[0],
                "to_ip": c.ip_address,
                "from_uplink": prev[1],
                "to_uplink": c.uplink_device_external_id,
            }
            await self.publish(ev.CLIENT_MOVED, "client", c.id, payload)
        if self.client_history:
            await self._record_sighting(c)

    async def _record_sighting(self, c: Client) -> None:
        if self._sightings is None:
            # Latest sighting per client in this site, loaded once per sync.
            rows = await self.db.scalars(
                select(ClientSighting)
                .join(Client, Client.id == ClientSighting.client_id)
                .where(Client.site_id == self.site.id)
                .order_by(ClientSighting.last_seen_at)
            )
            self._sightings = {r.client_id: r for r in rows}
        last = self._sightings.get(c.id)
        if last and (last.ip_address, last.uplink_device_external_id, last.type) == (c.ip_address, c.uplink_device_external_id, c.type):
            last.last_seen_at = self.now
            return
        row = ClientSighting(
            client_id=c.id,
            ip_address=c.ip_address,
            uplink_device_external_id=c.uplink_device_external_id,
            type=c.type,
            first_seen_at=self.now,
            last_seen_at=self.now,
            is_demo=self.controller.is_demo,
        )
        self.db.add(row)
        self._sightings[c.id] = row

    async def apply_network(self, n: Network, item: dict[str, Any], created: bool) -> None:
        n.name = item.get("name", "")
        n.management = item.get("management", "UNMANAGED")
        n.vlan_id = item.get("vlanId")
        n.enabled = bool(item.get("enabled", True))
        n.is_default = bool(item.get("default"))
        n.zone_external_id = item.get("zoneId")
        n.origin = (item.get("metadata") or {}).get("origin")
        n.ipv4 = item.get("ipv4Configuration")

    async def apply_wan(self, w: Wan, item: dict[str, Any], created: bool) -> None:
        w.name = item.get("name", "")

    async def apply_zone(self, z: FirewallZone, item: dict[str, Any], created: bool) -> None:
        z.name = item.get("name", "")
        z.origin = (item.get("metadata") or {}).get("origin")
        z.network_external_ids = item.get("networkIds") or []

    async def apply_policy(self, p: FirewallPolicy, item: dict[str, Any], created: bool) -> None:
        p.name = item.get("name", "")
        p.enabled = bool(item.get("enabled", True))
        p.action = (item.get("action") or {}).get("type", "UNKNOWN")
        p.index = item.get("index")
        p.source_zone_external_id = (item.get("source") or {}).get("zoneId")
        p.destination_zone_external_id = (item.get("destination") or {}).get("zoneId")
        p.logging_enabled = bool(item.get("loggingEnabled"))
        p.origin = (item.get("metadata") or {}).get("origin")


async def _try(caps: dict[str, Any], cap_id: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run a read only when the capability is supported; record probe results."""
    cap = caps.get(cap_id)
    if cap is None or cap["status"] == "unsupported":
        return None
    try:
        result = await fn()
    except UniFiError as exc:
        if exc.kind == "not_found":
            cap["status"] = "unsupported"
            cap["probe_error"] = "endpoint returned 404"
            return None
        raise
    cap["verified"] = True
    return result


async def sync_controller(
    db: AsyncSession, state: AppState, controller: Controller, *, inventory: bool, client_history: bool = False
) -> dict[str, Any]:
    api_key = await state.vault.reveal(db, controller.credential_id) if controller.credential_id else ""
    prev_status = controller.status
    summary: dict[str, Any] = {"controller": controller.name}
    try:
        async with state.unifi_client(controller, api_key) as api:
            summary.update(await _sync(db, state, controller, api, inventory=inventory, client_history=client_history))
        controller.status, controller.status_detail = "ok", ""
    except UniFiError as exc:
        controller.status = "unreachable" if exc.kind in ("unreachable", "tls") else "degraded"
        controller.status_detail = str(exc)[:500]
        summary["error"] = str(exc)
    controller.last_sync_at = utcnow()
    if prev_status != controller.status:
        await state.bus.publish(
            db,
            Event(
                type=ev.CONTROLLER_STATUS_CHANGED,
                source="unifi-network",
                entity_type="controller",
                entity_id=controller.id,
                payload={"from": prev_status, "to": controller.status, "detail": controller.status_detail},
                severity="info" if controller.status == "ok" else "warning",
            ),
        )
    await db.flush()
    return summary


async def _sync(
    db: AsyncSession, state: AppState, controller: Controller, api: UniFiNetworkClient, *, inventory: bool, client_history: bool
) -> dict[str, Any]:
    info = await api.info()
    version = info.get("applicationVersion")
    report = capabilities.evaluate(version)
    caps = report["capabilities"]
    caps["network.info.read"]["verified"] = True
    controller.application_version = version
    source = ("simulator" if controller.is_demo else "unifi-integration-api") + f"@{version}"

    site_items = await _try(caps, "network.sites.read", api.sites) or []
    existing = {s.external_id: s for s in await db.scalars(select(Site).where(Site.controller_id == controller.id))}
    out: dict[str, Any] = {"application_version": version, "sites": len(site_items)}
    now = utcnow()
    for item in site_items:
        site = existing.get(item["id"]) or Site(controller_id=controller.id, external_id=item["id"], first_seen_at=now)
        site.name = item.get("name", "")
        site.internal_reference = item.get("internalReference")
        site.source, site.is_demo, site.observed_at, site.present, site.raw = source, controller.is_demo, now, True, item
        db.add(site)
        await db.flush()
        if not inventory:
            continue
        ss = SiteSync(db, state, controller, site, source, client_history=client_history)
        sid = item["id"]
        devices = await _try(caps, "network.devices.read", partial(api.devices, sid))
        if devices is not None:
            # List responses omit uplink; fetch details for full fidelity.
            detailed = [await api.device(sid, d["id"]) for d in devices]
            await ss.upsert(Device, detailed, ss.apply_device)
            await _sync_stats(db, caps, api, site)
        if (clients := await _try(caps, "network.clients.read", partial(api.clients, sid))) is not None:
            await ss.upsert(Client, clients, ss.apply_client)
        if (networks := await _try(caps, "network.networks.read", partial(api.networks, sid))) is not None:
            detailed = [await api.network(sid, n["id"]) for n in networks]
            await ss.upsert(Network, detailed, ss.apply_network)
        if (wans := await _try(caps, "network.wans.read", partial(api.wans, sid))) is not None:
            await ss.upsert(Wan, wans, ss.apply_wan)
        if (zones := await _try(caps, "network.firewall.zones.read", partial(api.firewall_zones, sid))) is not None:
            await ss.upsert(FirewallZone, zones, ss.apply_zone)
        if (policies := await _try(caps, "network.firewall.policies.read", partial(api.firewall_policies, sid))) is not None:
            await ss.upsert(FirewallPolicy, policies, ss.apply_policy)
        out.setdefault("counts", {})[site.name] = ss.counts

    controller.capabilities = report
    controller.capabilities_checked_at = now
    return out


async def _sync_stats(db: AsyncSession, caps: dict[str, Any], api: UniFiNetworkClient, site: Site) -> None:
    if caps["network.devices.stats.read"]["status"] != "supported":
        return
    devices = await db.scalars(select(Device).where(Device.site_id == site.id, Device.present.is_(True)))
    for d in devices:
        if d.state != "ONLINE":
            continue
        try:
            d.stats = await api.device_stats(site.external_id, d.external_id)
            d.stats_observed_at = utcnow()
            caps["network.devices.stats.read"]["verified"] = True
        except UniFiError as exc:
            if exc.kind != "not_found":
                raise


async def sync_job(ctx: JobContext) -> dict[str, Any]:
    inventory = await ctx.state.modules.is_enabled(ctx.db, "inventory")
    client_history = inventory and await ctx.state.modules.is_enabled(ctx.db, "client-intel")
    q = select(Controller)
    if cid := ctx.params.get("controller_id"):
        q = q.where(Controller.id == cid)
    results = []
    for controller in await ctx.db.scalars(q):
        results.append(await sync_controller(ctx.db, ctx.state, controller, inventory=inventory, client_history=client_history))
        await ctx.state.bus.publish(
            ctx.db,
            Event(
                type=ev.CONTROLLER_SYNC_COMPLETED,
                source="unifi-network",
                entity_type="controller",
                entity_id=controller.id,
                payload={"status": controller.status},
            ),
        )
    return {"controllers": results}

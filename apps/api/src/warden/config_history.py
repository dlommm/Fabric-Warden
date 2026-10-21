"""Configuration snapshots and semantic diffs.

The snapshot holds only configuration, never state: a device going offline or
a client roaming is not a configuration change. Objects are keyed by their
controller id so a rename shows as a changed name, not a remove and an add.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.models import ConfigSnapshot, Device, FirewallPolicy, FirewallZone, Network, Site, Wan

SECTIONS = ("networks", "zones", "policies", "wans", "devices")
SECTION_LABEL = {
    "networks": "Networks",
    "zones": "Firewall zones",
    "policies": "Firewall policies",
    "wans": "WANs",
    "devices": "Device firmware",
}


def _dhcp(ipv4: dict[str, Any] | None) -> dict[str, Any]:
    cfg = ipv4 or {}
    dhcp = cfg.get("dhcpConfiguration") or {}
    rng = dhcp.get("ipAddressRange") or {}
    return {
        "gateway": cfg.get("hostIpAddress"),
        "prefix_length": cfg.get("prefixLength"),
        "dhcp_mode": dhcp.get("mode"),
        "dhcp_range": f"{rng['start']}-{rng['stop']}" if rng.get("start") else None,
    }


async def build_content(db: AsyncSession, site: Site) -> dict[str, dict[str, Any]]:
    zones = {
        z.external_id: z
        for z in await db.scalars(select(FirewallZone).where(FirewallZone.site_id == site.id, FirewallZone.present.is_(True)))
    }
    zone_name = lambda ext: zones[ext].name if ext in zones else ext  # noqa: E731
    out: dict[str, dict[str, Any]] = {s: {} for s in SECTIONS}
    for n in await db.scalars(select(Network).where(Network.site_id == site.id, Network.present.is_(True))):
        out["networks"][n.external_id] = {
            "name": n.name,
            "vlan_id": n.vlan_id,
            "enabled": n.enabled,
            "management": n.management,
            "zone": zone_name(n.zone_external_id) if n.zone_external_id else None,
            **_dhcp(n.ipv4),
        }
    for z in zones.values():
        out["zones"][z.external_id] = {"name": z.name, "networks": sorted(z.network_external_ids or [])}
    for p in await db.scalars(select(FirewallPolicy).where(FirewallPolicy.site_id == site.id, FirewallPolicy.present.is_(True))):
        raw = p.raw or {}
        out["policies"][p.external_id] = {
            "name": p.name,
            "enabled": p.enabled,
            "action": p.action,
            "index": p.index,
            "source_zone": zone_name(p.source_zone_external_id),
            "destination_zone": zone_name(p.destination_zone_external_id),
            "source_filter": (raw.get("source") or {}).get("trafficFilter"),
            "destination_filter": (raw.get("destination") or {}).get("trafficFilter"),
            "ip_scope": raw.get("ipProtocolScope"),
            "logging": p.logging_enabled,
            "origin": p.origin,
        }
    for w in await db.scalars(select(Wan).where(Wan.site_id == site.id, Wan.present.is_(True))):
        out["wans"][w.external_id] = {"name": w.name}
    for d in await db.scalars(select(Device).where(Device.site_id == site.id, Device.present.is_(True))):
        out["devices"][d.external_id] = {"name": d.name, "model": d.model, "firmware": d.firmware_version}
    return out


def content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def diff(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per section: objects added, removed and changed (with the fields that changed)."""
    old = old or {s: {} for s in SECTIONS}
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for section in SECTIONS:
        a, b = old.get(section, {}), new.get(section, {})
        added = [{"id": k, "name": b[k].get("name"), "value": b[k]} for k in b.keys() - a.keys()]
        removed = [{"id": k, "name": a[k].get("name"), "value": a[k]} for k in a.keys() - b.keys()]
        changed = []
        for k in a.keys() & b.keys():
            fields = [
                {"field": f, "from": a[k].get(f), "to": b[k].get(f)}
                for f in sorted(a[k].keys() | b[k].keys())
                if a[k].get(f) != b[k].get(f)
            ]
            if fields:
                changed.append({"id": k, "name": b[k].get("name"), "fields": fields})
        if added or removed or changed:
            key = lambda x: str(x["name"] or x["id"]).lower()  # noqa: E731
            out[section] = {"added": sorted(added, key=key), "removed": sorted(removed, key=key), "changed": sorted(changed, key=key)}
    return out


def counts(d: dict[str, dict[str, list[Any]]]) -> dict[str, dict[str, int]]:
    return {s: {k: len(v) for k, v in parts.items()} for s, parts in d.items()}


async def capture(db: AsyncSession, site: Site, *, trigger: str = "sync", source: str = "") -> ConfigSnapshot | None:
    """Store a snapshot when the site's configuration differs from the last one. Returns it, or None if unchanged."""
    content = await build_content(db, site)
    h = content_hash(content)
    last = await db.scalar(
        select(ConfigSnapshot).where(ConfigSnapshot.site_id == site.id).order_by(ConfigSnapshot.taken_at.desc()).limit(1)
    )
    if last is not None and last.content_hash == h and trigger == "sync":
        return None
    d = diff(last.content if last else None, content)
    snap = ConfigSnapshot(
        site_id=site.id,
        content_hash=h,
        content=content,
        summary={s: len(content[s]) for s in SECTIONS},
        change_counts=counts(d) if last else {},
        source=source,
        trigger=trigger,
        is_demo=site.is_demo,
        is_baseline=last is None,  # the first snapshot is the baseline until someone pins another
    )
    db.add(snap)
    await db.flush()
    return snap

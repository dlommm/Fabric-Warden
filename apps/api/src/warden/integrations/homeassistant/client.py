"""Read-only Home Assistant REST API collector.

GET /api/, /api/config and /api/states only (see official_api_matrix.json
next to this file). Out of every state, only selected categories are kept:
temperature sensors, UPS sensors (inferred by name), connectivity binary
sensors and router-based device trackers. Attributes are allow-listed, and GPS
trackers are skipped, so no location is collected.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from warden.integrations.hosts.clients import HostError, ReadOnlyHTTP
from warden.integrations.protect.client import norm_mac

KEEP_ATTRS = ("friendly_name", "unit_of_measurement", "device_class", "state_class", "source_type", "ip", "mac", "host_name")
UPS_WORD = re.compile(r"(^|[^a-z0-9])ups([^a-z0-9]|$)", re.I)
MAX_ENTITIES = 2000


def headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass
class EntityData:
    entity_id: str
    domain: str
    category: str
    basis: str  # device_class | domain | name
    name: str
    state: str
    unit: str
    device_class: str
    attributes: dict[str, Any]
    last_changed: datetime | None

    @property
    def external_id(self) -> str:
        return hashlib.sha1(self.entity_id.encode(), usedforsecurity=False).hexdigest()


@dataclass
class HaSnapshot:
    version: str
    location_name: str = ""
    temperature_unit: str = ""
    total_states: int = 0
    entities: list[EntityData] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _when(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def classify(entity_id: str, attrs: dict[str, Any]) -> tuple[str, str] | None:
    """(category, basis) for an entity Fabric Warden keeps, or None."""
    domain = entity_id.split(".", 1)[0]
    dc = str(attrs.get("device_class") or "")
    if domain == "sensor":
        if dc == "temperature":
            return "temperature", "device_class"
        if UPS_WORD.search(entity_id.split(".", 1)[-1].replace("_", " ")) or UPS_WORD.search(str(attrs.get("friendly_name") or "")):
            return "ups", "name"
    if domain == "binary_sensor" and dc == "connectivity":
        return "connectivity", "device_class"
    if domain == "device_tracker":
        src = str(attrs.get("source_type") or "").lower()
        if src == "gps":
            return None
        if src == "router" or attrs.get("ip") or attrs.get("mac"):
            return "tracker", "domain"
    return None


def select_entity(s: dict[str, Any]) -> EntityData | None:
    eid = str(s.get("entity_id") or "")
    if "." not in eid:
        return None
    attrs = s.get("attributes") or {}
    picked = classify(eid, attrs)
    if picked is None:
        return None
    kept = {k: attrs[k] for k in KEEP_ATTRS if k in attrs and attrs[k] is not None}
    if "mac" in kept:
        kept["mac"] = norm_mac(str(kept["mac"])) or str(kept["mac"])
    return EntityData(
        entity_id=eid[:255],
        domain=eid.split(".", 1)[0],
        category=picked[0],
        basis=picked[1],
        name=str(attrs.get("friendly_name") or eid)[:255],
        state=str(s.get("state", ""))[:255],
        unit=str(attrs.get("unit_of_measurement") or "")[:32],
        device_class=str(attrs.get("device_class") or "")[:64],
        attributes=kept,
        last_changed=_when(s.get("last_changed")),
    )


async def collect_home_assistant(api: ReadOnlyHTTP) -> HaSnapshot:
    root = (await api.get("/api/")).json()
    if not isinstance(root, dict) or "message" not in root:
        raise HostError("The answer is not the Home Assistant REST API", kind="protocol")
    cfg = (await api.get("/api/config")).json() or {}
    snap = HaSnapshot(
        version=str(cfg.get("version", "")),
        location_name=str(cfg.get("location_name", "")),
        temperature_unit=str((cfg.get("unit_system") or {}).get("temperature", "")),
    )
    states = (await api.get("/api/states")).json()
    if not isinstance(states, list):
        raise HostError("GET /api/states did not return a list", kind="protocol")
    snap.total_states = len(states)
    for s in states:
        if isinstance(s, dict) and (e := select_entity(s)):
            snap.entities.append(e)
    if len(snap.entities) > MAX_ENTITIES:
        snap.notes.append(f"Only the first {MAX_ENTITIES} of {len(snap.entities)} selected entities are kept.")
        snap.entities = snap.entities[:MAX_ENTITIES]
    return snap

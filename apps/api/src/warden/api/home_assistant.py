"""Home Assistant: connections, selected sensors and trackers, matched to network clients. Read-only.

Router-based device trackers report the IP, MAC and hostname Home Assistant
sees; they are matched to UniFi clients by IP, then MAC, then hostname. UPS
sensors are recognised by name (Home Assistant has no UPS device class), so
that category is labelled inferred.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.api.deps import DB, Allowed, require_module
from warden.api.integration_connections import add_connection_routes
from warden.db import utcnow
from warden.integrations.homeassistant.simulator import SIM_HA_TOKEN
from warden.integrations.hosts.sync import ClientIndex
from warden.models import Client, HaEntity, IntegrationConnection
from warden.security.rbac import P

router = APIRouter(prefix="/api/home-assistant", tags=["home-assistant"], dependencies=[Depends(require_module("home-assistant"))])
Reader = Allowed(P.INVENTORY_READ)
STALE = timedelta(minutes=10)
Category = Literal["ups", "temperature", "tracker", "connectivity"]
TEMP_WARN_C = 40.0
UPS_BATTERY_LOW = 50.0
ON_BATTERY = re.compile(r"on ?battery|discharg|^ob\b", re.I)

add_connection_routes(
    router, "home-assistant", "home_assistant", demo=("home-assistant (demo)", "http://home-assistant-simulator.local:8123", SIM_HA_TOKEN)
)


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def entities_out(db: AsyncSession, category: str | None = None) -> list[dict[str, Any]]:
    q = select(HaEntity).where(HaEntity.present.is_(True))
    if category:
        q = q.where(HaEntity.category == category)
    rows = list(await db.scalars(q.order_by(HaEntity.category, HaEntity.name)))
    index = ClientIndex(list(await db.scalars(select(Client).where(Client.present.is_(True)))))
    conns = {c.id: c for c in await db.scalars(select(IntegrationConnection).where(IntegrationConnection.kind == "home-assistant"))}
    now = utcnow()
    out = []
    for e in rows:
        a = e.attributes or {}
        client, reason = (None, None)
        if e.category == "tracker":
            client, reason = index.match(
                [a["ip"]] if a.get("ip") else [], [a["mac"]] if a.get("mac") else [], [str(a.get("host_name") or "")]
            )
        c = conns.get(e.connection_id)
        out.append(
            {
                "id": e.id,
                "entity_id": e.entity_id,
                "domain": e.domain,
                "category": e.category,
                "category_basis": e.category_basis,
                "name": e.name,
                "state": e.state,
                "value": _num(e.state),
                "unit": e.unit,
                "device_class": e.device_class,
                "attributes": a,
                "last_changed": e.last_changed,
                "available": e.state not in ("unavailable", "unknown"),
                "client": (
                    {"id": client.id, "name": client.alias or client.name, "ip_address": client.ip_address, "match": reason}
                    if client
                    else None
                ),
                "connection": {"id": c.id, "name": c.name, "status": c.status} if c else None,
                "provenance": {
                    "source": e.source,
                    "observed_at": e.observed_at,
                    "first_seen_at": e.first_seen_at,
                    "present": e.present,
                    "is_demo": e.is_demo,
                    "age_seconds": int((now - e.observed_at).total_seconds()),
                    "stale": e.observed_at < now - STALE,
                },
                "is_demo": e.is_demo,
            }
        )
    return out


@router.get("/entities")
async def list_entities(db: DB, principal: Reader, category: Category | None = None) -> dict:
    items = await entities_out(db, category)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["category"]] = counts.get(i["category"], 0) + 1
    return {
        "items": items,
        "counts": counts,
        "notes": [
            "Only temperature sensors, UPS sensors, connectivity sensors and router-based trackers are kept; GPS trackers are skipped.",
            "UPS sensors are recognised by the word 'ups' in their id or name (inferred).",
        ],
    }


async def ha_findings(db: AsyncSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in await db.scalars(select(IntegrationConnection).where(IntegrationConnection.kind == "home-assistant")):
        if c.status in ("unreachable", "degraded"):
            out.append(
                {"kind": "connection", "severity": "critical" if c.status == "unreachable" else "warning", "title": f"{c.name}: {c.status}",
                 "detail": c.status_detail, "target": {"type": "connection", "id": c.id, "name": c.name}, "is_demo": c.is_demo}
            )  # fmt: skip

    def add(kind: str, sev: str, e: dict[str, Any], title: str, detail: str) -> None:
        tgt = {"type": "entity", "id": e["id"], "name": e["name"], "entity_id": e["entity_id"]}
        out.append({"kind": kind, "severity": sev, "title": title, "detail": detail, "target": tgt, "is_demo": e["is_demo"]})

    for e in await entities_out(db):
        v, cat = e["value"], e["category"]
        if not e["available"]:
            if cat != "tracker":
                add("unavailable", "info", e, f"{e['name']} is {e['state']}", "Home Assistant has no current value for this entity.")
            continue
        if cat == "ups":
            if ON_BATTERY.search(e["state"]):
                add("ups_on_battery", "critical", e, f"{e['name']}: {e['state']}", "The UPS reports it is running on battery.")
            elif e["device_class"] == "battery" and v is not None and v < UPS_BATTERY_LOW:
                add(
                    "ups_battery_low",
                    "warning",
                    e,
                    f"{e['name']}: {e['state']}{e['unit']}",
                    f"Battery charge below {UPS_BATTERY_LOW:.0f} %.",
                )
        elif cat == "temperature" and v is not None:
            c_val = (v - 32) * 5 / 9 if e["unit"] == "°F" else v
            if c_val >= TEMP_WARN_C:
                add("temperature_high", "warning", e, f"{e['name']}: {e['state']} {e['unit']}", f"At or above {TEMP_WARN_C:.0f} °C.")
        elif cat == "connectivity" and e["state"] == "off":
            add("connectivity_down", "warning", e, f"{e['name']} is disconnected", "The connectivity sensor reports off.")
        elif cat == "tracker" and e["client"] is None and e["state"] == "home":
            add(
                "tracker_not_on_network", "info", e, f"{e['name']} is home but not in the network inventory",
                "No UniFi client matches its IP, MAC or hostname.",
            )  # fmt: skip
    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (order.get(f["severity"], 3), f["title"]))
    return out


@router.get("/findings")
async def findings(db: DB, principal: Reader) -> dict:
    items = await ha_findings(db)
    return {"items": items, "counts": {s: sum(f["severity"] == s for f in items) for s in ("critical", "warning", "info")}}

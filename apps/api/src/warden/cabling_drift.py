"""Cabling drift as events: what changed between documented cabling and what the controller reports.

After each sync, the cabling findings (a documented port reporting link down, a documented path that is
not an observed uplink, an observed uplink with no documented cable) are compared with the ones already
open. A new finding publishes cabling.finding.opened; one that has cleared publishes
cabling.finding.resolved. Which findings are open is read back from those events, so nothing else is
stored. Automation rules and notifications can react to both; nothing here writes to a controller.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import events as ev
from warden.app_state import AppState
from warden.events import Event
from warden.models import EventRecord

SEVERITY = {"info": "info", "warning": "warning"}


async def open_findings(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Findings whose last event is "opened", keyed by finding key, with that event's payload."""
    rows = await db.scalars(
        select(EventRecord)
        .where(EventRecord.type.in_((ev.CABLING_FINDING_OPENED, ev.CABLING_FINDING_RESOLVED)))
        .order_by(EventRecord.occurred_at)
    )
    state: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = (r.payload or {}).get("key")
        if not key:
            continue
        if r.type == ev.CABLING_FINDING_OPENED:
            state[key] = r.payload
        else:
            state.pop(key, None)
    return state


async def publish_drift(db: AsyncSession, state: AppState) -> dict[str, int]:
    """Publish opened and resolved cabling findings since the last run."""
    from warden.api.cabling import compute_findings, load_plant, undocumented_uplinks

    plant = await load_plant(db)
    current = {f["key"]: f for f in [*compute_findings(plant), *undocumented_uplinks(plant)]}
    before = await open_findings(db)
    opened = resolved = 0
    for key, f in current.items():
        if key in before:
            continue
        payload = {
            "key": key,
            "kind": f["kind"],
            "title": f["title"],
            **({"cable_id": f["cable_id"]} if f.get("cable_id") else {}),
            **({"device_id": f["device"]["id"], "uplink_id": f["uplink"]["id"]} if f.get("device") else {}),
        }
        await state.bus.publish(
            db,
            Event(
                type=ev.CABLING_FINDING_OPENED,
                source="cabling",
                entity_type="cable" if f.get("cable_id") else "device",
                entity_id=f.get("cable_id") or (f.get("device") or {}).get("id"),
                severity=SEVERITY.get(f["severity"], "info"),
                payload=payload,
            ),
        )
        opened += 1
    for key, payload in before.items():
        if key in current:
            continue
        await state.bus.publish(
            db,
            Event(
                type=ev.CABLING_FINDING_RESOLVED,
                source="cabling",
                entity_type="cable" if payload.get("cable_id") else "device",
                entity_id=payload.get("cable_id") or payload.get("device_id"),
                payload={**payload, "title": f"Cleared: {payload.get('title', key)}"},
            ),
        )
        resolved += 1
    return {"opened": opened, "resolved": resolved}

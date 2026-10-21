"""Typed in-process event bus.

Events are persisted (for history/timelines) and fanned out to in-process
subscribers and live SSE listeners. A failing subscriber is logged and never
breaks the publisher, so one misbehaving module cannot take down the core.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import new_id, utcnow
from warden.models import EventRecord

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    site_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    severity: str = "info"
    id: str = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "payload": self.payload,
            "site_id": self.site_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "severity": self.severity,
            "occurred_at": self.occurred_at.isoformat(),
        }


# Event types published by the core. Modules declare theirs in their manifest.
DEVICE_DISCOVERED = "inventory.device.discovered"
DEVICE_STATE_CHANGED = "inventory.device.state_changed"
DEVICE_FIRMWARE_CHANGED = "inventory.device.firmware_changed"
DEVICE_MISSING = "inventory.device.missing"
CLIENT_CONNECTED = "inventory.client.connected"
CLIENT_DISCONNECTED = "inventory.client.disconnected"
CLIENT_MOVED = "inventory.client.moved"  # address or attachment point changed
CONTROLLER_STATUS_CHANGED = "controller.status_changed"
CONTROLLER_SYNC_COMPLETED = "controller.sync.completed"
MODULE_ENABLED = "module.enabled"
ALERT_FIRING = "monitoring.alert.firing"
ALERT_RESOLVED = "monitoring.alert.resolved"
MODULE_DISABLED = "module.disabled"

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._listeners: set[asyncio.Queue[Event]] = set()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """`event_type` may end in `*` to match a prefix."""
        self._subs[event_type].append(handler)

    def listen(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        self._listeners.add(q)
        return q

    def unlisten(self, q: asyncio.Queue[Event]) -> None:
        self._listeners.discard(q)

    async def publish(self, db: AsyncSession, event: Event) -> None:
        db.add(
            EventRecord(
                id=event.id,
                type=event.type,
                occurred_at=event.occurred_at,
                source=event.source,
                site_id=event.site_id,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                severity=event.severity,
                payload=event.payload,
            )
        )
        await self.dispatch(event)

    async def dispatch(self, event: Event) -> None:
        for pattern, handlers in list(self._subs.items()):
            if pattern == event.type or (pattern.endswith("*") and event.type.startswith(pattern[:-1])):
                for h in handlers:
                    try:
                        await h(event)
                    except Exception:
                        log.exception("event handler failed for %s", event.type)
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop rather than block the publisher.
                pass

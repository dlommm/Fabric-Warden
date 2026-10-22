"""Deterministic Home Assistant simulator (demo and tests; served in-process).

Answers GET /api/, /api/config and /api/states in the documented shape, with a
Bearer token check. Trackers line up with the UniFi simulator's clients
(phone-a by IP and MAC, phone-b by MAC) and include one tracker the network
does not report and one GPS tracker that Fabric Warden must skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException

SIM_HA_TOKEN = "demo-home-assistant-long-lived-token"  # noqa: S105 - simulator-only demo token
T0 = "2026-10-21T06:00:00+00:00"


def _s(entity_id: str, state: str, **attrs: Any) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs,
        "last_changed": T0,
        "last_reported": T0,
        "last_updated": T0,
        "context": {"id": "01J9Z7K1M3S8TQ0Y6E4H2B5N7C", "parent_id": None, "user_id": None},
    }


def _states() -> list[dict[str, Any]]:
    return [
        _s("sun.sun", "above_horizon", friendly_name="Sun", elevation=21.4),
        _s("light.kitchen", "off", friendly_name="Kitchen"),
        _s(
            "sensor.rack_temperature",
            "31.5",
            friendly_name="Rack temperature",
            device_class="temperature",
            unit_of_measurement="°C",
            state_class="measurement",
        ),
        _s(
            "sensor.office_temperature",
            "22.1",
            friendly_name="Office temperature",
            device_class="temperature",
            unit_of_measurement="°C",
            state_class="measurement",
        ),
        _s(
            "sensor.garage_temperature",
            "unavailable",
            friendly_name="Garage temperature",
            device_class="temperature",
            unit_of_measurement="°C",
        ),
        _s("sensor.office_humidity", "48", friendly_name="Office humidity", device_class="humidity", unit_of_measurement="%"),
        _s("sensor.rack_ups_status", "Online", friendly_name="Rack UPS Status"),
        _s(
            "sensor.rack_ups_battery_charge",
            "100",
            friendly_name="Rack UPS Battery charge",
            device_class="battery",
            unit_of_measurement="%",
        ),
        _s("sensor.rack_ups_load", "34", friendly_name="Rack UPS Load", unit_of_measurement="%"),
        _s(
            "sensor.rack_ups_battery_runtime",
            "2460",
            friendly_name="Rack UPS Battery runtime",
            device_class="duration",
            unit_of_measurement="s",
        ),
        _s("sensor.groups_count", "4", friendly_name="Groups count"),  # contains "ups" inside a word: not a UPS
        _s("binary_sensor.internet", "on", friendly_name="Internet", device_class="connectivity"),
        _s("binary_sensor.nas_backup_ping", "off", friendly_name="nas-backup ping", device_class="connectivity"),
        _s("binary_sensor.front_door", "off", friendly_name="Front door", device_class="door"),
        _s(
            "device_tracker.phone_a",
            "home",
            friendly_name="phone-a",
            source_type="router",
            ip="10.0.20.114",
            mac="02:C1:00:00:00:0E",
            host_name="phone-a",
        ),
        _s("device_tracker.phone_b", "home", friendly_name="phone-b", source_type="router", mac="02:C1:00:00:00:0F"),
        _s("device_tracker.old_tablet", "not_home", friendly_name="old-tablet", source_type="router", mac="02:C1:00:00:00:EE"),
        _s(
            "device_tracker.alex_phone_gps",
            "home",
            friendly_name="Alex",
            source_type="gps",
            latitude=52.37,
            longitude=4.89,
            gps_accuracy=12,
        ),
    ]


@dataclass
class HaSimState:
    version: str = "2026.10.1"
    states: list[dict[str, Any]] = field(default_factory=_states)
    requests: list[tuple[str, str]] = field(default_factory=list)


def create_home_assistant_app(sim: HaSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def record(request, call_next):  # type: ignore[no-untyped-def]
        sim.requests.append((request.method, request.url.path))
        return await call_next(request)

    def auth(header: str | None) -> None:
        if header != f"Bearer {SIM_HA_TOKEN}":
            raise HTTPException(401, "Unauthorized")

    @app.get("/api/")
    async def root(authorization: str | None = Header(default=None)) -> dict:
        auth(authorization)
        return {"message": "API running."}

    @app.get("/api/config")
    async def config(authorization: str | None = Header(default=None)) -> dict:
        auth(authorization)
        return {
            "location_name": "Demo Home",
            "unit_system": {"length": "km", "mass": "g", "temperature": "°C", "volume": "L"},
            "time_zone": "Europe/Amsterdam",
            "version": sim.version,
            "components": ["api", "frontend", "http", "nut", "device_tracker", "unifi"],
            "state": "RUNNING",
        }

    @app.get("/api/states")
    async def states(authorization: str | None = Header(default=None)) -> list:
        auth(authorization)
        return sim.states

    return app

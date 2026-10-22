"""Deterministic UniFi Protect simulator (demo and tests; served in-process).

Responses follow the published Protect Integration API 7.3.70 schema for
meta/info, cameras and nvrs. Only GET is answered. The MAC addresses line up
with the UniFi Network simulator's camera clients, so cameras match the
network inventory: three wired cameras on the Rack PoE Switch, a Wi-Fi
doorbell, and one camera that the network does not report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException

SIM_PROTECT_KEY = "demo-protect-api-key"
PREFIX = "/proxy/protect/integration"


def _cam(n: int, name: str, model: str, mac: str, state: str, smart: list[str], mic: bool = True) -> dict[str, Any]:
    return {
        "id": f"66f0c0de000000000000{n:04x}",
        "modelKey": "camera",
        "state": state,
        "name": name,
        "type": model,
        "guid": None,
        "mac": mac,
        "isMicEnabled": mic,
        "micVolume": 100,
        "videoMode": "default",
        "hdrType": "auto",
        "featureFlags": {
            "supportFullHdSnapshot": True,
            "hasHdr": True,
            "smartDetectTypes": smart,
            "smartDetectAudioTypes": [],
            "videoModes": ["default"],
            "hasMic": True,
            "hasLedStatus": True,
            "hasSpeaker": "Doorbell" in model,
        },
        "smartDetectSettings": {"objectTypes": smart[:2], "audioTypes": []},
        "hasPackageCamera": "Doorbell" in model,
    }


def _cameras() -> list[dict[str, Any]]:
    # MACs 02:c1:00:00:00:07/08/09/15 are the UniFi simulator's camera clients.
    return [
        _cam(1, "cam-driveway", "UVC G5 Bullet", "02C100000007", "CONNECTED", ["person", "vehicle", "licensePlate"]),
        _cam(2, "cam-backyard", "UVC G4 Pro", "02C100000008", "CONNECTED", ["person", "vehicle", "animal"]),
        _cam(3, "cam-front-door", "UVC G5 Turret Ultra", "02C100000009", "DISCONNECTED", ["person", "vehicle"]),
        _cam(4, "doorbell", "UVC G4 Doorbell Pro", "02C100000015", "CONNECTED", ["person", "package"], mic=False),
        _cam(5, "cam-garage", "UVC G5 Flex", "02C1000000F1", "CONNECTED", ["person"]),
    ]


def _nvr() -> dict[str, Any]:
    return {
        "id": "66f0c0de00000000000000ff",
        "modelKey": "nvr",
        "name": "unvr-protect",
        "type": "UNVR",
        "guid": None,
        "mac": "02C100000004",
        "doorbellSettings": {
            "defaultMessageText": "Welcome",
            "defaultMessageResetTimeoutMs": 60000,
            "customMessages": [],
            "customImages": [],
        },
        "armMode": {
            "status": "disabled",
            "armProfileId": None,
            "armedAt": None,
            "willBeArmedAt": None,
            "breachDetectedAt": None,
            "breachEventCount": 0,
            "breachTriggerEventId": None,
            "breachEventId": None,
        },
    }


@dataclass
class ProtectSimState:
    version: str = "7.3.70"
    cameras: list[dict[str, Any]] = field(default_factory=_cameras)
    nvr: dict[str, Any] = field(default_factory=_nvr)
    requests: list[tuple[str, str]] = field(default_factory=list)


def create_protect_app(sim: ProtectSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def record(request, call_next):  # type: ignore[no-untyped-def]
        sim.requests.append((request.method, request.url.path))
        return await call_next(request)

    def auth(key: str | None) -> None:
        if key != SIM_PROTECT_KEY:
            raise HTTPException(401, "Unauthorized")

    @app.get(f"{PREFIX}/v1/meta/info")
    async def info(x_api_key: str | None = Header(default=None)) -> dict:
        auth(x_api_key)
        return {"applicationVersion": sim.version}

    @app.get(f"{PREFIX}/v1/cameras")
    async def cameras(x_api_key: str | None = Header(default=None)) -> list:
        auth(x_api_key)
        return sim.cameras

    @app.get(f"{PREFIX}/v1/nvrs")
    async def nvrs(x_api_key: str | None = Header(default=None)) -> dict:
        auth(x_api_key)
        return sim.nvr

    return app

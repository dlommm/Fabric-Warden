"""Process-wide services shared by routers and jobs."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI

from warden.config import Settings
from warden.db import Database
from warden.events import EventBus
from warden.integrations.unifi.client import UniFiNetworkClient
from warden.integrations.unifi.simulator import SimulatorState, create_simulator_app
from warden.jobs import Scheduler
from warden.models import Controller
from warden.modules.service import ModuleService
from warden.security.vault import Vault

SIMULATOR_BASE_URL = "http://unifi-simulator.local"


@dataclass
class AppState:
    settings: Settings
    db: Database
    vault: Vault
    bus: EventBus
    modules: ModuleService
    scheduler: Scheduler = field(init=False)
    simulator: SimulatorState = field(default_factory=SimulatorState)
    _sim_app: FastAPI | None = None

    def simulator_transport(self) -> httpx.AsyncBaseTransport:
        if self._sim_app is None:
            self._sim_app = create_simulator_app(self.simulator)
        return httpx.ASGITransport(app=self._sim_app)

    def unifi_client(self, controller: Controller, api_key: str) -> UniFiNetworkClient:
        if controller.kind == "unifi-simulator":
            return UniFiNetworkClient(SIMULATOR_BASE_URL, api_key, transport=self.simulator_transport())
        return UniFiNetworkClient(
            controller.base_url,
            api_key,
            verify_tls=controller.verify_tls,
            ca_bundle_pem=controller.ca_bundle_pem,
        )

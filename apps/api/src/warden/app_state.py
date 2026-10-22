"""Process-wide services shared by routers and jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI

from warden.ai.simulator import AiSimState, create_openai_app
from warden.config import Settings
from warden.db import Database
from warden.events import EventBus
from warden.integrations.hosts.simulators import (
    HostSimState,
    create_docker_app,
    create_node_exporter_app,
    create_proxmox_app,
    create_unraid_app,
)
from warden.integrations.snmp.simulator import SnmpSimState
from warden.integrations.unifi.client import UniFiNetworkClient
from warden.integrations.unifi.simulator import SimulatorState, create_simulator_app
from warden.jobs import Scheduler
from warden.models import AiProvider, Controller, HostConnection
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
    host_sim: HostSimState = field(default_factory=HostSimState)
    syslog: Any = None  # warden.syslog.Ingest, created in build_state
    snmp_sim: SnmpSimState = field(default_factory=SnmpSimState)
    _host_sim_apps: dict[str, FastAPI] = field(default_factory=dict)
    # Seams for outbound I/O so tests never reach a real network.
    http_transport: httpx.AsyncBaseTransport | None = None  # notification webhooks / ntfy
    smtp_send: Callable[[dict[str, Any], str, Any], Awaitable[None]] | None = None  # (config, password, EmailMessage)
    probe_connect: Callable[[str, int, float], Awaitable[float | None]] | None = None  # host, port, timeout -> ms
    snmp_session: Callable[[str, int, Any], Any] | None = None  # host, port, Credentials -> snmp Session
    ai_transport: httpx.AsyncBaseTransport | None = None  # AI providers (tests point this at an in-process fake)
    ai_sim: AiSimState = field(default_factory=AiSimState)
    _ai_sim_app: FastAPI | None = None
    ai_stop: bool = False  # in-process mirror of the AI kill switch, checked between agent turns

    def simulator_transport(self) -> httpx.AsyncBaseTransport:
        if self._sim_app is None:
            self._sim_app = create_simulator_app(self.simulator)
        return httpx.ASGITransport(app=self._sim_app)

    def host_transport(self, conn: HostConnection) -> httpx.AsyncBaseTransport | None:
        """Demo connections are served by in-process simulators; real ones use the network."""
        if not conn.is_demo:
            return None
        if conn.kind not in self._host_sim_apps:
            factory = {
                "proxmox": create_proxmox_app,
                "unraid": create_unraid_app,
                "docker": create_docker_app,
                "node_exporter": create_node_exporter_app,
            }
            self._host_sim_apps[conn.kind] = factory[conn.kind](self.host_sim)
        return httpx.ASGITransport(app=self._host_sim_apps[conn.kind])

    def ai_transport_for(self, provider: AiProvider) -> httpx.AsyncBaseTransport | None:
        """The demo provider is the in-process offline model; real providers use the network (or the test seam)."""
        if provider.is_demo:
            if self._ai_sim_app is None:
                self._ai_sim_app = create_openai_app(self.ai_sim)
            return httpx.ASGITransport(app=self._ai_sim_app)
        return self.ai_transport

    def unifi_client(self, controller: Controller, api_key: str) -> UniFiNetworkClient:
        if controller.kind == "unifi-simulator":
            return UniFiNetworkClient(SIMULATOR_BASE_URL, api_key, transport=self.simulator_transport())
        return UniFiNetworkClient(
            controller.base_url,
            api_key,
            verify_tls=controller.verify_tls,
            ca_bundle_pem=controller.ca_bundle_pem,
        )

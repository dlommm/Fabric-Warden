"""Deterministic simulated UniFi Network controller.

Serves a subset of the official Integration API with synthetic, sanitized
fixtures modelled on a home-lab topology (gateway, aggregation and PoE
switches, APs, VLANs, zone firewall). Used for tests and the labelled demo
controller; it is never presented as live data.

Endpoints absent from the simulated application version return 404, which
lets tests exercise capability gating against older controllers.
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request

from warden.integrations.unifi.capabilities import matrix, parse_version

SIM_API_KEY = "simulator-demo-key"
SITE_ID = "88f7af54-98f8-306a-a1c7-c9349722b1f6"


def _uuid(n: int, kind: int) -> str:
    return f"{kind:08x}-0000-4000-8000-{n:012x}"


def _mac(n: int) -> str:
    return f"02:5a:00:00:{n >> 8:02x}:{n & 0xFF:02x}"


def _port(idx: int, connector: str, max_speed: int, speed: int | None, poe: bool = False, poe_on: bool = False) -> dict[str, Any]:
    p: dict[str, Any] = {
        "idx": idx,
        "connector": connector,
        "maxSpeedMbps": max_speed,
        "state": "UP" if speed else "DOWN",
    }
    if speed:
        p["speedMbps"] = speed
    if poe:
        p["poe"] = {"enabled": True, "standard": "802.3bt", "type": 3, "state": "UP" if poe_on else "DOWN"}
    return p


def _build_devices() -> list[dict[str, Any]]:
    gw_ports = [_port(i, "RJ45", 2500 if i < 9 else 10000, 1000 if i in (1, 2, 5) else None) for i in range(1, 10)]
    gw_ports += [_port(10, "SFPPLUS", 10000, 10000), _port(11, "SFP28", 25000, 25000), _port(12, "SFP28", 25000, None)]
    agg_ports = [_port(i, "SFPPLUS", 10000, 10000 if i <= 6 else None) for i in range(1, 25)]
    agg_ports += [_port(i, "SFP28", 25000, 25000 if i == 25 else None) for i in range(25, 29)]
    poe_ports = [
        _port(
            i,
            "RJ45",
            2500,
            2500 if i in (1, 2, 3, 4, 7, 9) else (1000 if i in (11, 12) else None),
            poe=True,
            poe_on=i in (1, 2, 3, 4, 7, 9),
        )
        for i in range(1, 17)
    ]
    poe_ports += [_port(17, "SFPPLUS", 10000, 10000), _port(18, "SFPPLUS", 10000, None)]
    rack_ports = [_port(i, "RJ45", 1000, 1000 if i % 3 else None, poe=i <= 8, poe_on=i in (1, 2)) for i in range(1, 25)]
    rack_ports += [_port(i, "SFPPLUS", 10000, 10000 if i == 25 else None) for i in (25, 26)]

    def radios(*specs: tuple[float, int, int, str]) -> list[dict[str, Any]]:
        return [{"frequencyGHz": f, "channel": c, "channelWidthMHz": w, "wlanStandard": s} for f, c, w, s in specs]

    devices = [
        dict(
            n=1,
            name="Gateway",
            model="UDM Beast",
            ip="10.0.1.1",
            fw="5.0.12",
            # As a real UDM Beast reports it on 10.6.106: no "gateway" feature, although the schema has one.
            features=["switching"],
            ports=gw_ports,
            uplink=None,
            state="ONLINE",
        ),
        dict(
            n=2,
            name="Core Aggregation",
            model="USW Pro Aggregation",
            ip="10.0.1.2",
            fw="7.2.123",
            features=["switching"],
            ports=agg_ports,
            uplink=1,
            state="ONLINE",
        ),
        dict(
            n=3,
            name="Rack PoE Switch",
            model="USW Pro Max 16 PoE",
            ip="10.0.1.3",
            fw="7.2.123",
            features=["switching"],
            ports=poe_ports,
            uplink=2,
            state="ONLINE",
        ),
        dict(
            n=4,
            name="Office Switch",
            model="USW Pro 24 PoE",
            ip="10.0.1.4",
            fw="7.1.26",
            features=["switching"],
            ports=rack_ports,
            uplink=2,
            state="ONLINE",
        ),
        dict(
            n=5,
            name="AP Living Room",
            model="U7 Pro",
            ip="10.0.1.21",
            fw="8.0.18",
            features=["accessPoint"],
            ports=[_port(1, "RJ45", 2500, 2500)],
            uplink=3,
            state="ONLINE",
            radios=radios((2.4, 6, 20, "802.11be"), (5.0, 36, 160, "802.11be"), (6.0, 37, 320, "802.11be")),
        ),
        dict(
            n=6,
            name="AP Office",
            model="U7 Pro",
            ip="10.0.1.22",
            fw="8.0.18",
            features=["accessPoint"],
            ports=[_port(1, "RJ45", 2500, 2500)],
            uplink=3,
            state="ONLINE",
            radios=radios((2.4, 11, 20, "802.11be"), (5.0, 100, 80, "802.11be"), (6.0, 69, 160, "802.11be")),
        ),
        dict(
            n=7,
            name="AP Garage",
            model="U6 Mesh",
            ip="10.0.1.23",
            fw="6.6.77",
            features=["accessPoint"],
            ports=[_port(1, "RJ45", 1000, 1000)],
            uplink=4,
            state="CONNECTION_INTERRUPTED",
            radios=radios((2.4, 1, 20, "802.11ax"), (5.0, 149, 80, "802.11ax")),
        ),
        dict(
            n=8,
            name="AP Patio",
            model="U7 Outdoor",
            ip="10.0.1.24",
            fw="8.0.18",
            features=["accessPoint"],
            ports=[_port(1, "RJ45", 2500, 2500)],
            uplink=3,
            state="ONLINE",
            radios=radios((2.4, 1, 20, "802.11be"), (5.0, 52, 80, "802.11be")),
        ),
    ]
    out = []
    for d in devices:
        out.append(
            {
                "id": _uuid(d["n"], 0xD),
                "macAddress": _mac(d["n"]),
                "ipAddress": d["ip"],
                "name": d["name"],
                "model": d["model"],
                "state": d["state"],
                "supported": True,
                "firmwareVersion": d["fw"],
                "firmwareUpdatable": d["n"] in (4, 7),
                "features": d["features"],
                "interfaces": {"ports": d["ports"], "radios": d.get("radios", [])},
                "uplink": {"deviceId": _uuid(d["uplink"], 0xD)} if d["uplink"] else None,
                "adoptedAt": "2025-03-14T09:00:00Z",
                "provisionedAt": "2026-10-01T02:00:00Z",
                "configurationId": f"cfg-{d['n']:04d}",
            }
        )
    return out


NETWORKS = [
    # n, name, vlan, subnet host, prefix, zone
    (1, "Default", 1, "10.0.0.1", 24, "internal", True),
    (2, "Management", 10, "10.0.1.1", 24, "internal", False),
    (3, "Trusted", 20, "10.0.20.1", 24, "internal", False),
    (4, "Servers", 30, "10.0.30.1", 24, "servers", False),
    (5, "IoT", 40, "10.0.40.1", 23, "iot", False),
    (6, "Cameras", 50, "10.0.50.1", 24, "iot", False),
    (7, "Guest", 60, "10.0.60.1", 24, "hotspot", False),
]

ZONES = [
    ("internal", "Internal", "SYSTEM_DEFINED"),
    ("external", "External", "SYSTEM_DEFINED"),
    ("gateway", "Gateway", "SYSTEM_DEFINED"),
    ("vpn", "Vpn", "SYSTEM_DEFINED"),
    ("hotspot", "Hotspot", "SYSTEM_DEFINED"),
    ("dmz", "Dmz", "SYSTEM_DEFINED"),
    ("iot", "IoT", "USER_DEFINED"),
    ("servers", "Servers", "USER_DEFINED"),
]


def _zone_id(key: str) -> str:
    return _uuid([z[0] for z in ZONES].index(key) + 1, 0xF2)


def _build_networks() -> list[dict[str, Any]]:
    out = []
    for n, name, vlan, host, prefix, zone, default in NETWORKS:
        base = host.rsplit(".", 1)[0]
        out.append(
            {
                "management": "GATEWAY",
                "id": _uuid(n, 0xE),
                "name": name,
                "enabled": True,
                "vlanId": vlan,
                "default": default,
                "zoneId": _zone_id(zone),
                "metadata": {"origin": "SYSTEM_DEFINED" if default else "USER_DEFINED"},
                "internetAccessEnabled": True,
                "isolationEnabled": False,
                "cellularBackupEnabled": True,
                "mdnsForwardingEnabled": zone != "hotspot",
                "ipv4Configuration": {
                    "hostIpAddress": host,
                    "prefixLength": prefix,
                    "autoScaleEnabled": False,
                    "dhcpConfiguration": {
                        "mode": "SERVER",
                        "ipAddressRange": {"start": f"{base}.100", "stop": f"{base}.250"},
                        "leaseTimeSeconds": 86400,
                    },
                },
            }
        )
    return out


def _build_zones() -> list[dict[str, Any]]:
    by_zone: dict[str, list[str]] = {}
    for n, _name, _v, _h, _p, zone, _d in NETWORKS:
        by_zone.setdefault(zone, []).append(_uuid(n, 0xE))
    return [
        {"id": _zone_id(k), "name": name, "networkIds": by_zone.get(k, []), "metadata": {"origin": origin}} for k, name, origin in ZONES
    ]


def _policy(
    n: int,
    name: str,
    src: str,
    dst: str,
    action: str,
    *,
    logging: bool = False,
    origin: str = "USER_DEFINED",
    enabled: bool = True,
    dst_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    destination: dict[str, Any] = {"zoneId": _zone_id(dst)}
    if dst_filter:
        destination["trafficFilter"] = dst_filter
    return {
        "id": _uuid(n, 0xF1),
        "name": name,
        "enabled": enabled,
        "index": 10000 + n if origin == "USER_DEFINED" else None,
        "action": {"type": action},
        "source": {"zoneId": _zone_id(src)},
        "destination": destination,
        "ipProtocolScope": {"ipVersion": "IPV4_AND_IPV6"},
        "connectionStateFilter": ["NEW"],
        "loggingEnabled": logging,
        "metadata": {"origin": origin},
    }


def _ports(*ports: int) -> dict[str, Any]:
    """A destination port filter in the Integration API's shape (Firewall policy port filter)."""
    return {
        "type": "PORT",
        "portFilter": {"type": "PORTS", "matchOpposite": False, "items": [{"type": "PORT_NUMBER", "value": p} for p in ports]},
    }


def _build_policies() -> list[dict[str, Any]]:
    return [
        _policy(1, "Allow established/related", "internal", "external", "ALLOW", origin="SYSTEM_DEFINED"),
        _policy(2, "Block IoT to Internal", "iot", "internal", "BLOCK", logging=True),
        _policy(3, "Allow Internal to IoT", "internal", "iot", "ALLOW"),
        _policy(4, "Allow Internal to Servers", "internal", "servers", "ALLOW"),
        _policy(5, "Block Servers to Internal", "servers", "internal", "BLOCK", logging=True),
        _policy(6, "Block Guest to Gateway admin", "hotspot", "gateway", "REJECT"),
        _policy(7, "Legacy allow IoT to Servers", "iot", "servers", "ALLOW", enabled=False),
        # Placed after "Block IoT to Internal", so it never matches (shadowed).
        _policy(8, "Allow IoT to Internal DNS", "iot", "internal", "ALLOW", dst_filter=_ports(53)),
        # Covered by "Allow Internal to IoT" with the same action (redundant).
        _policy(9, "Allow Internal to IoT web", "internal", "iot", "ALLOW", dst_filter=_ports(80, 443)),
        # UniFi adds a derived "(Return)" policy for return traffic of some user rules and keeps it in step.
        _policy(10, "Allow Internal to Servers (Return)", "servers", "internal", "ALLOW", origin="DERIVED"),
    ]


CLIENT_SPECS = [
    # n, name, type, network n, uplink device n
    (1, "unraid-tower", "WIRED", 4, 2),
    (2, "proxmox-01", "WIRED", 4, 2),
    (3, "proxmox-02", "WIRED", 4, 2),
    (4, "unvr-protect", "WIRED", 6, 3),
    (5, "workstation", "WIRED", 3, 4),
    (6, "nas-backup", "WIRED", 4, 2),
    (7, "cam-driveway", "WIRED", 6, 3),
    (8, "cam-backyard", "WIRED", 6, 3),
    (9, "cam-front-door", "WIRED", 6, 3),
    (10, "printer", "WIRED", 3, 4),
    (11, "home-assistant", "WIRED", 4, 3),
    (12, "docker-host", "WIRED", 4, 2),
    (13, "laptop-work", "WIRELESS", 3, 6),
    (14, "phone-a", "WIRELESS", 3, 5),
    (15, "phone-b", "WIRELESS", 3, 5),
    (16, "tablet", "WIRELESS", 3, 5),
    (17, "tv-living", "WIRELESS", 5, 5),
    (18, "thermostat", "WIRELESS", 5, 5),
    (19, "smart-plug-01", "WIRELESS", 5, 8),
    (20, "smart-plug-02", "WIRELESS", 5, 8),
    (21, "doorbell", "WIRELESS", 6, 8),
    (22, "speaker-kitchen", "WIRELESS", 5, 5),
    (23, "guest-phone", "WIRELESS", 7, 6),
    (24, "laptop-personal", "WIRELESS", 3, 6),
    (25, "robot-vacuum", "WIRELESS", 5, 5),
    (26, "vpn-roadwarrior", "VPN", 3, 1),
]


def _build_clients() -> list[dict[str, Any]]:
    out = []
    for n, name, ctype, net, up in CLIENT_SPECS:
        host = next(h for nn, _nm, _v, h, _p, _z, _d in NETWORKS if nn == net)
        ip = f"{host.rsplit('.', 1)[0]}.{100 + n}"
        c: dict[str, Any] = {
            "type": ctype,
            "id": _uuid(n, 0xC),
            "name": name,
            "connectedAt": f"2026-10-{(n % 18) + 1:02d}T{n % 24:02d}:15:00Z",
            "ipAddress": ip,
            "access": {"type": "GUEST" if net == 7 else "DEFAULT"},
        }
        if ctype in ("WIRED", "WIRELESS"):
            c["macAddress"] = f"02:c1:00:00:00:{n:02x}"
            c["uplinkDeviceId"] = _uuid(up, 0xD)
        out.append(c)
    return out


@dataclass
class SimulatorState:
    version: str = "10.6.106"
    devices: list[dict[str, Any]] = field(default_factory=_build_devices)
    clients: list[dict[str, Any]] = field(default_factory=_build_clients)
    networks: list[dict[str, Any]] = field(default_factory=_build_networks)
    zones: list[dict[str, Any]] = field(default_factory=_build_zones)
    policies: list[dict[str, Any]] = field(default_factory=_build_policies)
    wans: list[dict[str, Any]] = field(
        default_factory=lambda: [{"id": _uuid(1, 0xA), "name": "Primary (WAN1)"}, {"id": _uuid(2, 0xA), "name": "Backup (WAN2)"}]
    )
    # Fixed clock for deterministic statistics in tests; None = wall clock.
    clock: float | None = None
    # Writes the simulator has accepted, newest last, so tests can see exactly what was sent.
    writes: list[dict[str, Any]] = field(default_factory=list)
    # Faults tests can switch on: "patch_ignored" (PATCH/PUT answer 200 but change nothing),
    # "put_alters_other_field" (a PUT also changes a field it was not asked to),
    # "restart_never_returns" (a restarted device stays offline), "writes_fail" (every write answers 500).
    faults: set[str] = field(default_factory=set)

    def supports(self, method: str, template: str) -> bool:
        e = matrix().get((method, template))
        return e is not None and parse_version(self.version) >= parse_version(e["introduced"])

    def now(self) -> float:
        return self.clock if self.clock is not None else time.time()

    def settle(self) -> None:
        """Bring restarted devices back once their restart time has passed."""
        for d in self.devices:
            back = d.get("_back_at")
            if back is not None and self.now() >= back and "restart_never_returns" not in self.faults:
                d["state"] = "ONLINE"
                d.pop("_back_at")


def _page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    chunk = items[offset : offset + limit]
    return {"offset": offset, "limit": limit, "count": len(chunk), "totalCount": len(items), "data": copy.deepcopy(chunk)}


RESTART_SECONDS = 90


def _overview(device: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "macAddress",
        "ipAddress",
        "name",
        "model",
        "state",
        "supported",
        "firmwareVersion",
        "firmwareUpdatable",
        "features",
        "interfaces",
    )
    return {k: device[k] for k in keys}


def _stats(device: dict[str, Any], now: float) -> dict[str, Any]:
    n = int(device["id"][-12:], 16)
    # A daily load cycle per device plus a little short-term jitter, so sampled
    # history looks like a network rather than noise.
    day = 2 * math.pi * (now % 86_400) / 86_400 + n
    phase = now / 900.0 + n
    cpu = round(12 + 8 * n % 20 + 7 * math.sin(day) + 1.5 * math.sin(phase), 1)
    mem = round(35 + 4 * n % 30 + 3 * math.sin(day + 1) + 0.5 * math.cos(phase), 1)
    stats: dict[str, Any] = {
        "uptimeSec": int(1_200_000 + n * 86_400 + now % 86_400),
        "lastHeartbeatAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7)),
        "nextHeartbeatAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 23)),
        "loadAverage1Min": round(0.2 + n * 0.05 + 0.12 * math.sin(day) + 0.03 * math.sin(phase), 2),
        "loadAverage5Min": round(0.2 + n * 0.05, 2),
        "loadAverage15Min": round(0.18 + n * 0.05, 2),
        "cpuUtilizationPct": max(cpu, 1.0),
        "memoryUtilizationPct": max(mem, 5.0),
        "interfaces": {
            "radios": [
                {
                    "frequencyGHz": r["frequencyGHz"],
                    "txRetriesPct": round(4 + 2.5 * abs(math.sin(day + r["frequencyGHz"])) + 0.5 * abs(math.sin(phase)), 1),
                }
                for r in device["interfaces"]["radios"]
            ]
        },
    }
    if device.get("uplink"):
        stats["uplink"] = {
            "txRateBps": int(4e6 + 3e6 * abs(math.sin(day)) + 4e5 * abs(math.sin(phase))),
            "rxRateBps": int(2.5e7 + 2e7 * abs(math.sin(day)) + 2e6 * abs(math.cos(phase))),
        }
    return stats


def create_simulator_app(state: SimulatorState | None = None) -> FastAPI:
    sim = state or SimulatorState()
    app = FastAPI(title="UniFi Network API simulator", openapi_url=None, docs_url=None, redoc_url=None)
    app.state.sim = sim
    prefix = "/proxy/network/integration"

    def gate(request: Request, method: str, template: str, key: str | None) -> None:
        if key != SIM_API_KEY:
            raise HTTPException(401, "Unauthorized")
        if not sim.supports(method, template):
            raise HTTPException(404, "Not Found")

    def site(site_id: str) -> None:
        if site_id != SITE_ID:
            raise HTTPException(404, "site not found")

    K = Header(default=None, alias="X-API-KEY")

    @app.get(prefix + "/v1/info")
    def info(request: Request, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", "/v1/info", x_api_key)
        return {"applicationVersion": sim.version}

    @app.get(prefix + "/v1/sites")
    def sites(request: Request, offset: int = 0, limit: int = Query(25, le=200), x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", "/v1/sites", x_api_key)
        return _page([{"id": SITE_ID, "internalReference": "default", "name": "Home Lab"}], offset, limit)

    def listing(template: str, getter):  # type: ignore[no-untyped-def]
        def handler(
            request: Request, siteId: str, offset: int = 0, limit: int = Query(25, le=200), x_api_key: str | None = K
        ) -> dict[str, Any]:
            gate(request, "GET", template, x_api_key)
            site(siteId)
            return _page(getter(), offset, limit)

        app.get(prefix + template)(handler)

    S = "/v1/sites/{siteId}"

    def settled_devices() -> list[dict[str, Any]]:
        sim.settle()
        return [_overview(d) for d in sim.devices]

    listing(S + "/devices", settled_devices)
    listing(S + "/clients", lambda: sim.clients)
    listing(S + "/networks", lambda: [{k: v for k, v in n.items() if k != "ipv4Configuration"} for n in sim.networks])
    listing(S + "/wans", lambda: sim.wans)
    listing(S + "/firewall/zones", lambda: sim.zones)
    listing(S + "/firewall/policies", lambda: sim.policies)

    def find(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
        for it in items:
            if it["id"] == item_id:
                return copy.deepcopy(it)
        raise HTTPException(404, "Not Found")

    @app.get(prefix + S + "/devices/{deviceId}")
    def device(request: Request, siteId: str, deviceId: str, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", S + "/devices/{deviceId}", x_api_key)
        site(siteId)
        sim.settle()
        return {k: v for k, v in find(sim.devices, deviceId).items() if not k.startswith("_")}

    @app.get(prefix + S + "/devices/{deviceId}/statistics/latest")
    def device_stats(request: Request, siteId: str, deviceId: str, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", S + "/devices/{deviceId}/statistics/latest", x_api_key)
        site(siteId)
        d = find(sim.devices, deviceId)
        if d["state"] != "ONLINE":
            raise HTTPException(404, "statistics unavailable")
        return _stats(d, sim.clock if sim.clock is not None else time.time())

    @app.get(prefix + S + "/networks/{networkId}")
    def network(request: Request, siteId: str, networkId: str, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", S + "/networks/{networkId}", x_api_key)
        site(siteId)
        return find(sim.networks, networkId)

    @app.get(prefix + S + "/firewall/policies/{policyId}")
    def firewall_policy(request: Request, siteId: str, policyId: str, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", S + "/firewall/policies/{firewallPolicyId}", x_api_key)
        site(siteId)
        return find(sim.policies, policyId)

    # -- the writes the change engine uses, with the published request bodies --------------

    def accept(method: str, template: str, request: Request, key: str | None, siteId: str, body: dict[str, Any]) -> None:
        gate(request, method, template, key)
        site(siteId)
        if "writes_fail" in sim.faults:
            raise HTTPException(500, "simulated controller error")
        sim.writes.append({"method": method, "path": request.url.path.removeprefix(prefix), "body": body, "at": sim.now()})

    def raw(it: dict[str, Any]) -> dict[str, Any]:
        for real in (sim.policies, sim.devices):
            for x in real:
                if x["id"] == it["id"]:
                    return x
        raise HTTPException(404, "Not Found")

    @app.patch(prefix + S + "/firewall/policies/{policyId}")
    async def patch_policy(request: Request, siteId: str, policyId: str, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if set(body) - {"loggingEnabled"} or not isinstance(body.get("loggingEnabled"), bool):
            raise HTTPException(400, "Only loggingEnabled can be patched")
        target = raw(find(sim.policies, policyId))
        accept("PATCH", S + "/firewall/policies/{firewallPolicyId}", request, x_api_key, siteId, body)
        if "patch_ignored" not in sim.faults:
            target["loggingEnabled"] = body["loggingEnabled"]
        return copy.deepcopy(target)

    # Update Network, gateway-managed variant (10.6.106): GET minus id, default and metadata.
    net_fields = {
        "management",
        "name",
        "enabled",
        "vlanId",
        "dhcpGuarding",
        "cellularBackupEnabled",
        "internetAccessEnabled",
        "ipv4Configuration",
        "ipv6Configuration",
        "isolationEnabled",
        "mdnsForwardingEnabled",
        "zoneId",
    }
    net_required = {"cellularBackupEnabled", "enabled", "internetAccessEnabled", "ipv4Configuration", "isolationEnabled", "name", "vlanId"}

    @app.put(prefix + S + "/networks/{networkId}")
    async def put_network(request: Request, siteId: str, networkId: str, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if unknown := set(body) - net_fields:
            raise HTTPException(400, f"Unrecognized fields: {', '.join(sorted(unknown))}")
        if missing := net_required - set(body):
            raise HTTPException(400, f"Missing required fields: {', '.join(sorted(missing))}")
        target = next((n for n in sim.networks if n["id"] == networkId), None)
        if target is None:
            raise HTTPException(404, "Not Found")
        if body.get("zoneId") and body["zoneId"] not in {z["id"] for z in sim.zones}:
            raise HTTPException(400, "Unknown zoneId")
        accept("PUT", S + "/networks/{networkId}", request, x_api_key, siteId, body)
        old_zone = target.get("zoneId")
        for k in net_fields:
            if k in body:
                target[k] = copy.deepcopy(body[k])
            else:
                target.pop(k, None)
        if "put_alters_other_field" in sim.faults:
            sim.faults.discard("put_alters_other_field")
            target["mdnsForwardingEnabled"] = not target.get("mdnsForwardingEnabled")
        if target.get("zoneId") != old_zone:  # the zone lists follow the network's zone
            for z in sim.zones:
                ids = [i for i in z["networkIds"] if i != networkId]
                z["networkIds"] = ids + ([networkId] if z["id"] == target["zoneId"] else [])
        return copy.deepcopy(target)

    @app.get(prefix + S + "/firewall/zones/{zoneId}")
    def firewall_zone(request: Request, siteId: str, zoneId: str, x_api_key: str | None = K) -> dict[str, Any]:
        gate(request, "GET", S + "/firewall/zones/{firewallZoneId}", x_api_key)
        site(siteId)
        return find(sim.zones, zoneId)

    @app.put(prefix + S + "/firewall/zones/{zoneId}")
    async def put_zone(request: Request, siteId: str, zoneId: str, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if set(body) != {"name", "networkIds"}:
            raise HTTPException(400, "Body must be exactly name and networkIds")
        target = next((z for z in sim.zones if z["id"] == zoneId), None)
        if target is None:
            raise HTTPException(404, "Not Found")
        if target["metadata"]["origin"] != "USER_DEFINED":
            raise HTTPException(400, "System zones cannot be updated")
        accept("PUT", S + "/firewall/zones/{firewallZoneId}", request, x_api_key, siteId, body)
        target["name"], target["networkIds"] = body["name"], list(body["networkIds"])
        return copy.deepcopy(target)

    # The published Update Firewall Policy schema (10.6.106): everything GET returns except id, index and metadata.
    put_fields = {
        "action",
        "connectionStateFilter",
        "description",
        "destination",
        "enabled",
        "ipProtocolScope",
        "ipsecFilter",
        "loggingEnabled",
        "name",
        "schedule",
        "source",
    }
    put_required = {"action", "destination", "enabled", "ipProtocolScope", "loggingEnabled", "name", "source"}

    @app.put(prefix + S + "/firewall/policies/{policyId}")
    async def put_policy(request: Request, siteId: str, policyId: str, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if unknown := set(body) - put_fields:
            raise HTTPException(400, f"Unrecognized fields: {', '.join(sorted(unknown))}")
        if missing := put_required - set(body):
            raise HTTPException(400, f"Missing required fields: {', '.join(sorted(missing))}")
        target = raw(find(sim.policies, policyId))
        accept("PUT", S + "/firewall/policies/{firewallPolicyId}", request, x_api_key, siteId, body)
        if "patch_ignored" in sim.faults:
            return copy.deepcopy(target)
        for k in put_fields:
            if k in body:
                target[k] = copy.deepcopy(body[k])
            else:
                target.pop(k, None)
        if "put_alters_other_field" in sim.faults:
            sim.faults.discard("put_alters_other_field")  # one-shot, so the restore that follows is clean
            target["connectionStateFilter"] = ["NEW", "INVALID"]
        return copy.deepcopy(target)

    @app.post(prefix + S + "/devices/{deviceId}/actions")
    async def device_action(request: Request, siteId: str, deviceId: str, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if body != {"action": "RESTART"}:
            raise HTTPException(400, "Unsupported action")
        target = raw(find(sim.devices, deviceId))
        accept("POST", S + "/devices/{deviceId}/actions", request, x_api_key, siteId, body)
        target["state"], target["_back_at"] = "GETTING_READY", sim.now() + RESTART_SECONDS
        return {}

    @app.post(prefix + S + "/devices/{deviceId}/interfaces/ports/{portIdx}/actions")
    async def port_action(request: Request, siteId: str, deviceId: str, portIdx: int, x_api_key: str | None = K) -> dict[str, Any]:
        body = await request.json()
        if body != {"action": "POWER_CYCLE"}:
            raise HTTPException(400, "Unsupported action")
        target = raw(find(sim.devices, deviceId))
        port = next((p for p in target["interfaces"]["ports"] if p["idx"] == portIdx), None)
        if port is None or not port.get("poe"):
            raise HTTPException(400, "Port has no PoE")
        accept("POST", S + "/devices/{deviceId}/interfaces/ports/{portIdx}/actions", request, x_api_key, siteId, body)
        return {}

    @app.api_route(prefix + "/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
    def refuse_writes(path: str) -> None:
        # Only the writes above are simulated; everything else stays refused.
        raise HTTPException(405, "simulator does not implement this write")

    return app

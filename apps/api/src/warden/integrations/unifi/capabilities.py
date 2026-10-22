"""Capability detection for the official UniFi Network Integration API.

Availability is computed from the endpoint matrix generated from Ubiquiti's
versioned OpenAPI documents (official_api_matrix.json), then refined by
probing read endpoints during sync. We never infer a capability from the
existence of a UI feature.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MATRIX_PATH = Path(__file__).with_name("official_api_matrix.json")

S = "/v1/sites/{siteId}"


@dataclass(frozen=True)
class CapabilityDef:
    id: str
    title: str
    endpoints: tuple[tuple[str, str], ...]
    write: bool = False


CAPABILITIES: tuple[CapabilityDef, ...] = (
    CapabilityDef("network.info.read", "Application info", (("GET", "/v1/info"),)),
    CapabilityDef("network.sites.read", "Sites", (("GET", "/v1/sites"),)),
    CapabilityDef("network.devices.read", "Adopted devices", (("GET", S + "/devices"), ("GET", S + "/devices/{deviceId}"))),
    CapabilityDef("network.devices.stats.read", "Latest device statistics", (("GET", S + "/devices/{deviceId}/statistics/latest"),)),
    CapabilityDef("network.devices.pending.read", "Devices pending adoption", (("GET", "/v1/pending-devices"),)),
    CapabilityDef("network.devices.restart", "Restart device", (("POST", S + "/devices/{deviceId}/actions"),), write=True),
    CapabilityDef(
        "network.devices.adopt", "Adopt / unadopt devices", (("POST", S + "/devices"), ("DELETE", S + "/devices/{deviceId}")), write=True
    ),
    CapabilityDef(
        "network.ports.power_cycle",
        "PoE power-cycle port",
        (("POST", S + "/devices/{deviceId}/interfaces/ports/{portIdx}/actions"),),
        write=True,
    ),
    CapabilityDef("network.clients.read", "Connected clients", (("GET", S + "/clients"), ("GET", S + "/clients/{clientId}"))),
    CapabilityDef("network.clients.guest_access", "Authorize guest access", (("POST", S + "/clients/{clientId}/actions"),), write=True),
    CapabilityDef("network.networks.read", "Networks / VLANs", (("GET", S + "/networks"), ("GET", S + "/networks/{networkId}"))),
    CapabilityDef(
        "network.networks.write",
        "Create / update networks",
        (("POST", S + "/networks"), ("PUT", S + "/networks/{networkId}"), ("DELETE", S + "/networks/{networkId}")),
        write=True,
    ),
    CapabilityDef("network.wans.read", "WAN interfaces", (("GET", S + "/wans"),)),
    CapabilityDef("network.wifi.read", "Wi-Fi broadcasts", (("GET", S + "/wifi/broadcasts"),)),
    CapabilityDef("network.wifi.write", "Edit Wi-Fi broadcasts", (("PUT", S + "/wifi/broadcasts/{wifiBroadcastId}"),), write=True),
    CapabilityDef("network.firewall.zones.read", "Firewall zones", (("GET", S + "/firewall/zones"),)),
    CapabilityDef("network.firewall.zones.write", "Edit firewall zones", (("PUT", S + "/firewall/zones/{firewallZoneId}"),), write=True),
    CapabilityDef(
        "network.firewall.policies.read",
        "Firewall policies",
        (("GET", S + "/firewall/policies"), ("GET", S + "/firewall/policies/ordering")),
    ),
    CapabilityDef(
        "network.firewall.policies.logging",
        "Turn firewall policy logging on or off",
        (("GET", S + "/firewall/policies/{firewallPolicyId}"), ("PATCH", S + "/firewall/policies/{firewallPolicyId}")),
        write=True,
    ),
    CapabilityDef(
        "network.networks.update",
        "Edit a network (name, internet access, isolation, zone)",
        (("GET", S + "/networks/{networkId}"), ("PUT", S + "/networks/{networkId}")),
        write=True,
    ),
    CapabilityDef(
        "network.firewall.zones.update",
        "Rename a custom firewall zone",
        (("GET", S + "/firewall/zones/{firewallZoneId}"), ("PUT", S + "/firewall/zones/{firewallZoneId}")),
        write=True,
    ),
    CapabilityDef(
        "network.firewall.policies.update",
        "Enable or disable a firewall policy",
        (("GET", S + "/firewall/policies/{firewallPolicyId}"), ("PUT", S + "/firewall/policies/{firewallPolicyId}")),
        write=True,
    ),
    CapabilityDef(
        "network.firewall.policies.write",
        "Edit / reorder firewall policies",
        (
            ("POST", S + "/firewall/policies"),
            ("PUT", S + "/firewall/policies/{firewallPolicyId}"),
            ("PUT", S + "/firewall/policies/ordering"),
        ),
        write=True,
    ),
    CapabilityDef("network.acl.read", "Switch ACL rules", (("GET", S + "/acl-rules"),)),
    CapabilityDef("network.dns.policies.read", "DNS policies", (("GET", S + "/dns/policies"),)),
    CapabilityDef("network.traffic_lists.read", "Traffic matching lists", (("GET", S + "/traffic-matching-lists"),)),
    CapabilityDef(
        "network.vpn.read", "VPN servers and site-to-site tunnels", (("GET", S + "/vpn/servers"), ("GET", S + "/vpn/site-to-site-tunnels"))
    ),
    CapabilityDef(
        "network.switching.read",
        "LAGs, MC-LAG and switch stacks",
        (("GET", S + "/switching/lags"), ("GET", S + "/switching/switch-stacks")),
    ),
    CapabilityDef("network.radius.read", "RADIUS profiles", (("GET", S + "/radius/profiles"),)),
    CapabilityDef("network.vouchers.read", "Hotspot vouchers", (("GET", S + "/hotspot/vouchers"),)),
    CapabilityDef("network.dpi.read", "DPI applications and categories", (("GET", "/v1/dpi/applications"), ("GET", "/v1/dpi/categories"))),
)

# Facts the official API does not expose at any known version. Listed so the
# UI can explain why a feature needs another collector.
NOT_EXPOSED = (
    ("sfp.optical_diagnostics", "SFP/SFP28 optical TX/RX power and temperature (DOM)"),
    ("port.fec", "Forward-error-correction mode / state"),
    ("port.error_counters", "Per-port CRC, symbol and discard counters"),
    ("port.traffic_counters", "Per-port byte/packet counters"),
    ("dhcp.leases", "DHCP lease table"),
    ("lldp.neighbors", "LLDP neighbour tables"),
    ("ids.events", "IDS/IPS threat events"),
    ("stats.history", "Historical statistics (only 'latest' is available)"),
)


def parse_version(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


@lru_cache
def matrix() -> dict[tuple[str, str], dict[str, Any]]:
    data = json.loads(MATRIX_PATH.read_text())
    return {(e["method"], e["path"]): e for e in data["endpoints"]}


@lru_cache
def known_spec_versions() -> tuple[str, ...]:
    return tuple(json.loads(MATRIX_PATH.read_text())["_provenance"]["spec_versions"])


def evaluate(app_version: str | None) -> dict[str, Any]:
    """Return a capability report for a given Network application version."""
    m = matrix()
    newest = max(known_spec_versions(), key=parse_version)
    v = parse_version(app_version) if app_version else None
    beyond_known = v is not None and v > parse_version(newest)
    caps: dict[str, Any] = {}
    for cap in CAPABILITIES:
        introduced = max((m[e]["introduced"] for e in cap.endpoints), key=parse_version)
        if v is None:
            status = "unknown"
        elif v >= parse_version(introduced):
            status = "supported"
        else:
            status = "unsupported"
        caps[cap.id] = {
            "title": cap.title,
            "status": status,
            "min_version": introduced,
            "write": cap.write,
            "verified": False,
        }
    return {
        "application_version": app_version,
        "newest_known_spec": newest,
        "beyond_known_spec": beyond_known,
        "capabilities": caps,
        "not_exposed": [{"id": i, "title": t} for i, t in NOT_EXPOSED],
    }

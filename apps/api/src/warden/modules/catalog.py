"""Bundled first-party module manifests and install presets.

Modules that are not built yet are declared with maturity=PLANNED so the
Module Center can show the roadmap and dependency graph honestly; they cannot
be enabled until their implementation lands.
"""

from __future__ import annotations

from warden.events import (
    CLIENT_CONNECTED,
    CLIENT_DISCONNECTED,
    CLIENT_MOVED,
    CONTROLLER_STATUS_CHANGED,
    CONTROLLER_SYNC_COMPLETED,
    DEVICE_DISCOVERED,
    DEVICE_FIRMWARE_CHANGED,
    DEVICE_MISSING,
    DEVICE_STATE_CHANGED,
    MODULE_DISABLED,
    MODULE_ENABLED,
)
from warden.modules.contracts import (
    Category,
    JobDecl,
    Maturity,
    ModuleManifest,
    NavEntry,
    ResourceEstimate,
    WidgetDecl,
)

C = Category
VERSION = "0.1.0"


def _planned(
    id: str,
    name: str,
    category: Category,
    phase: int,
    description: str,
    requires: list[str] | None = None,
    memory_mb: int = 64,
    containers: list[str] | None = None,
) -> ModuleManifest:
    return ModuleManifest(
        id=id,
        version=VERSION,
        name=name,
        category=category,
        description=description,
        maturity=Maturity.PLANNED,
        roadmap_phase=phase,
        requires=["core", *(requires or [])],
        resources=ResourceEstimate(memory_mb=memory_mb, extra_containers=containers or []),
    )


CORE = ModuleManifest(
    id="core",
    version=VERSION,
    name="Platform Core",
    category=C.CORE,
    description="Authentication, RBAC, credential vault, module lifecycle, event bus, scheduler, audit trail and health.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=1,
    always_enabled=True,
    publishes=[MODULE_ENABLED, MODULE_DISABLED],
    nav=[
        NavEntry(label="Overview", href="/", icon="gauge", section="Operate"),
        NavEntry(label="Module Center", href="/modules", icon="blocks", section="Platform"),
        NavEntry(label="Audit Trail", href="/audit", icon="scroll", section="Platform"),
        NavEntry(label="Users & Access", href="/settings/users", icon="users", section="Platform"),
    ],
    jobs=[JobDecl(id="core.housekeeping", title="Expire sessions and prune job history", default_interval_seconds=3600)],
    resources=ResourceEstimate(memory_mb=256, cpu_millicores=250),
    data_ownership=["users", "auth_sessions", "api_tokens", "credentials", "audit_records", "events", "job_runs"],
)

UNIFI_NETWORK = ModuleManifest(
    id="unifi-network",
    version=VERSION,
    name="UniFi Network Integration",
    category=C.INTEGRATION,
    description=(
        "Connects to UniFi Network controllers through the official Integration API (API key). "
        "Detects application version and per-endpoint capability; read-only by default."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=1,
    requires=["core"],
    controller_capabilities=["network.info.read", "network.sites.read"],
    nav=[NavEntry(label="Controllers", href="/controllers", icon="server", section="Platform")],
    jobs=[JobDecl(id="unifi-network.sync", title="Poll controllers and sync inventory", default_interval_seconds=60)],
    publishes=[CONTROLLER_STATUS_CHANGED, CONTROLLER_SYNC_COMPLETED],
    external_integrations=["UniFi Network Application >= 9.1 (Integration API)"],
    resources=ResourceEstimate(memory_mb=48, cpu_millicores=50),
    data_ownership=["controllers"],
    warnings=[
        "The official API does not expose SFP optical diagnostics, FEC state, port error counters, "
        "DHCP leases, LLDP neighbours, IDS/IPS events or historical statistics. Those need the "
        "syslog/SNMP/SSH collectors planned for later phases."
    ],
)

INVENTORY = ModuleManifest(
    id="inventory",
    version=VERSION,
    name="Asset & Device Inventory",
    category=C.NETWORK,
    description="Unified inventory of gateways, switches, access points, clients, networks and WANs with provenance and history.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=1,
    requires=["core", "unifi-network"],
    controller_capabilities=["network.devices.read", "network.clients.read"],
    nav=[
        NavEntry(label="Devices", href="/inventory/devices", icon="router", section="Network"),
        NavEntry(label="Clients", href="/inventory/clients", icon="laptop", section="Network"),
        NavEntry(label="Networks", href="/inventory/networks", icon="network", section="Network"),
    ],
    widgets=[
        WidgetDecl(id="inventory.device-health", title="Device health"),
        WidgetDecl(id="inventory.client-mix", title="Client connections"),
    ],
    publishes=[
        DEVICE_DISCOVERED,
        DEVICE_STATE_CHANGED,
        DEVICE_FIRMWARE_CHANGED,
        DEVICE_MISSING,
        CLIENT_CONNECTED,
        CLIENT_DISCONNECTED,
        CLIENT_MOVED,
    ],
    subscribes=[CONTROLLER_SYNC_COMPLETED],
    resources=ResourceEstimate(memory_mb=32, disk_mb_per_day=5),
    data_ownership=["sites", "devices", "ports", "radios", "clients", "networks", "wans", "firewall_zones", "firewall_policies"],
)

CLIENT_INTEL = ModuleManifest(
    id="client-intel",
    version=VERSION,
    name="Client Intelligence",
    category=C.NETWORK,
    description="Client 360 view with address and attachment history, identity (alias, owner, notes) and per-client timeline.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=2,
    requires=["core", "inventory"],
    controller_capabilities=["network.clients.read"],
    subscribes=[CLIENT_CONNECTED, CLIENT_DISCONNECTED, CLIENT_MOVED],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=2),
    data_ownership=["client_sightings"],
    uninstall_behavior="Disabling stops recording new sightings; recorded history is retained.",
    warnings=[
        "The official API exposes no device fingerprint, hostname source or DHCP lease data, "
        "so device-type confidence is not available. History begins when this module is enabled."
    ],
)

IPAM = ModuleManifest(
    id="ipam",
    version=VERSION,
    name="IP Address Management",
    category=C.NETWORK,
    description=(
        "Documented prefixes, VLANs and address reservations compared against observed networks and clients: "
        "utilisation, address maps and planned-vs-observed findings."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=2,
    requires=["core", "inventory"],
    controller_capabilities=["network.networks.read", "network.clients.read"],
    permissions=["ipam:edit"],
    nav=[
        NavEntry(label="IP Plan", href="/ipam", icon="grid", section="Network"),
        NavEntry(label="VLANs", href="/ipam/vlans", icon="layers", section="Network"),
    ],
    resources=ResourceEstimate(memory_mb=16),
    data_ownership=["ip_prefixes", "ip_reservations", "vlan_records"],
    warnings=[
        "IPAM records are documentation only and are never pushed to a controller. "
        "DHCP leases and controller-side fixed-IP reservations are not exposed by the official API, "
        "so lease state and controller reservations cannot be compared yet."
    ],
)

PLANNED: list[ModuleManifest] = [
    _planned(
        "analytics-studio",
        "Analytics Studio",
        C.OBSERVABILITY,
        3,
        "Native dashboard engine with drag-and-drop widgets, templates and drilldowns.",
        memory_mb=128,
    ),
    _planned(
        "monitoring",
        "Monitoring & Alerts",
        C.OBSERVABILITY,
        3,
        "Health, reachability, thresholds, maintenance windows and escalations.",
        ["inventory"],
    ),
    _planned("notifications", "Notification Channels", C.INTEGRATION, 3, "Email, webhook, ntfy, Matrix and other alert destinations."),
    _planned(
        "wan",
        "WAN & ISP Operations",
        C.NETWORK,
        3,
        "WAN health, latency/loss, opt-in speed and bufferbloat tests, failover history.",
        ["inventory"],
    ),
    _planned(
        "switching", "Switch Operations", C.NETWORK, 3, "Ports, PoE budgets, VLAN visibility, LAGs, flaps and utilisation.", ["inventory"]
    ),
    _planned(
        "wifi",
        "Wi-Fi Operations",
        C.NETWORK,
        3,
        "Radios, SSIDs, roaming history, sticky clients and tuning recommendations.",
        ["inventory"],
    ),
    _planned(
        "fiber",
        "Fiber & High-Speed Diagnostics",
        C.NETWORK,
        3,
        "Optics inventory, FEC state, optical power and physical-layer errors where hardware exposes them.",
        ["switching"],
    ),
    _planned(
        "dcim",
        "Rack & DCIM Manager",
        C.INFRASTRUCTURE,
        4,
        "Sites, rooms, rack elevations, drag-and-drop design and occupancy validation.",
        ["inventory"],
    ),
    _planned(
        "cabling", "Cabling & Fiber Documentation", C.INFRASTRUCTURE, 4, "Patch panels, cable tracing, schedules and labels.", ["dcim"]
    ),
    _planned("power", "Power & Environment", C.INFRASTRUCTURE, 4, "UPS/PDU inventory, power chains, PoE and load forecasting.", ["dcim"]),
    _planned(
        "servers", "Servers & Virtualization", C.INFRASTRUCTURE, 4, "Proxmox, Unraid, Docker and Linux host integrations.", ["inventory"]
    ),
    _planned(
        "topology", "Network Topology", C.NETWORK, 4, "Physical and logical topology with VLAN overlays and live health.", ["inventory"]
    ),
    _planned(
        "docs",
        "Documentation & Wiki",
        C.INFRASTRUCTURE,
        4,
        "Versioned Markdown runbooks and architecture pages with live inventory references.",
    ),
    _planned(
        "asset-lifecycle",
        "Asset Lifecycle",
        C.INFRASTRUCTURE,
        4,
        "Purchases, warranties, licences, spares and end-of-life tracking.",
        ["inventory"],
    ),
    _planned(
        "firewall",
        "Firewall & Policy Manager",
        C.SECURITY,
        5,
        "Zone policy inventory, ordering, shadowing analysis and change history.",
        ["inventory"],
    ),
    _planned(
        "flows",
        "Traffic & Flow Analytics",
        C.OBSERVABILITY,
        5,
        "IPFIX/NetFlow collection, top talkers and conversations.",
        memory_mb=512,
        containers=["flow-collector", "clickhouse"],
    ),
    _planned(
        "logs",
        "Log Management",
        C.OBSERVABILITY,
        5,
        "Syslog/CEF ingestion, parsing, search and alert rules.",
        memory_mb=256,
        containers=["syslog-collector"],
    ),
    _planned("soc", "Security Operations Center", C.SECURITY, 5, "Findings, posture, IDS/IPS events and security incidents.", ["logs"]),
    _planned("ndr", "Network Detection & Response", C.SECURITY, 5, "Explainable flow-based anomaly detection.", ["flows"]),
    _planned(
        "config-history",
        "Configuration History & Backup",
        C.AUTOMATION,
        5,
        "Snapshots, semantic diffs, drift and attribution.",
        ["unifi-network"],
    ),
    _planned("investigations", "Investigation Workbench", C.SECURITY, 5, "Pivot from alerts to clients, flows, logs and changes.", ["soc"]),
    _planned(
        "compliance", "Policy & Compliance", C.SECURITY, 5, "Custom baselines, evidence collection and drift alerts.", ["config-history"]
    ),
    _planned(
        "change-engine",
        "Change Approval & Recovery",
        C.AUTOMATION,
        6,
        "Central transactional write path with approvals, verification and guarded rollback.",
        ["unifi-network"],
    ),
    _planned(
        "bulk-ops", "Bulk Operations", C.AUTOMATION, 6, "Capability-verified bulk PoE, port and device operations.", ["change-engine"]
    ),
    _planned("automation", "Automation Studio", C.AUTOMATION, 6, "Workflows, triggers, approvals and dry runs.", ["change-engine"]),
    _planned("digital-twin", "Network Digital Twin", C.AUTOMATION, 6, "Versioned infrastructure graph and impact previews.", ["topology"]),
    _planned("ai-providers", "AI Providers", C.AI, 7, "Local (Ollama, LM Studio) and API model providers; official CLI setup guides."),
    _planned(
        "ai-agents",
        "AI Agent Center",
        C.AI,
        7,
        "Specialised network agents with autonomy levels, budgets and approval queues.",
        ["ai-providers"],
        memory_mb=256,
    ),
    _planned(
        "unifi-protect",
        "UniFi Protect",
        C.INTEGRATION,
        9,
        "Cameras, NVR health and PoE correlation via the official Protect Integration API.",
    ),
    _planned("home-assistant", "Home Assistant", C.INTEGRATION, 9, "Sensors and automations from Home Assistant."),
    _planned(
        "mcp", "MCP & Paperclip Gateway", C.AI, 9, "Expose permission-scoped tools to MCP clients and Paperclip agents.", ["ai-providers"]
    ),
]

CATALOG: dict[str, ModuleManifest] = {m.id: m for m in [CORE, UNIFI_NETWORK, INVENTORY, CLIENT_INTEL, IPAM, *PLANNED]}

PRESETS: dict[str, dict[str, object]] = {
    "minimal": {"name": "Minimal Core", "modules": ["core", "unifi-network", "inventory"]},
    "monitoring": {
        "name": "Network Monitoring",
        "modules": ["core", "unifi-network", "inventory", "monitoring", "analytics-studio", "wan", "notifications", "topology"],
    },
    "administrator": {
        "name": "Network Administrator",
        "modules": [
            "core",
            "unifi-network",
            "inventory",
            "ipam",
            "switching",
            "wifi",
            "firewall",
            "config-history",
            "change-engine",
            "bulk-ops",
        ],
    },
    "soc": {
        "name": "Security Operations Center",
        "modules": ["core", "unifi-network", "inventory", "firewall", "logs", "flows", "soc", "ndr", "investigations", "compliance"],
    },
    "dcim": {
        "name": "Infrastructure / DCIM",
        "modules": ["core", "unifi-network", "inventory", "dcim", "cabling", "power", "servers", "asset-lifecycle", "docs"],
    },
    "ai-ops": {
        "name": "AI Network Operations",
        "modules": ["core", "unifi-network", "inventory", "monitoring", "ai-providers", "ai-agents"],
    },
    "full": {"name": "Full Installation", "modules": list(CATALOG)},
}

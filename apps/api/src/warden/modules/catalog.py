"""Bundled first-party module manifests and install presets.

Modules that are not built yet are declared with maturity=PLANNED so the
Module Center can show the roadmap and dependency graph honestly; they cannot
be enabled until their implementation lands.
"""

from __future__ import annotations

from warden.events import (
    ALERT_FIRING,
    ALERT_RESOLVED,
    CLIENT_CONNECTED,
    CLIENT_DISCONNECTED,
    CLIENT_MOVED,
    CONFIG_CHANGED,
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
    jobs=[
        JobDecl(id="core.housekeeping", title="Expire sessions and prune job history", default_interval_seconds=3600),
        JobDecl(id="core.metrics", title="Roll up and prune metric history", default_interval_seconds=900),
    ],
    resources=ResourceEstimate(memory_mb=256, cpu_millicores=250),
    data_ownership=[
        "users",
        "auth_sessions",
        "api_tokens",
        "credentials",
        "audit_records",
        "events",
        "job_runs",
        "metric_series",
        "metric_samples",
    ],
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

ANALYTICS = ModuleManifest(
    id="analytics-studio",
    version=VERSION,
    name="Analytics Studio",
    category=C.OBSERVABILITY,
    description=(
        "Metric history for devices, radios, clients, networks and controllers, sampled on every sync, "
        "with dashboards built from widgets and starter templates."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=3,
    requires=["core", "inventory"],
    controller_capabilities=["network.devices.stats.read"],
    permissions=["analytics:edit"],
    nav=[NavEntry(label="Dashboards", href="/analytics", icon="chart", section="Operate")],
    resources=ResourceEstimate(memory_mb=64, disk_mb_per_day=20),
    data_ownership=["dashboards"],
    uninstall_behavior="Disabling hides dashboards. Metric history keeps recording while Monitoring or WAN is enabled.",
    warnings=[
        "The official API exposes only the latest statistics, so history starts when this module is enabled "
        "and has the sync interval's resolution (60 s). Port counters, per-client traffic and WAN throughput are not available."
    ],
)

MONITORING = ModuleManifest(
    id="monitoring",
    version=VERSION,
    name="Monitoring & Alerts",
    category=C.OBSERVABILITY,
    description="Threshold rules over metric history with pending, firing and resolved alerts, acknowledgement and maintenance windows.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=3,
    requires=["core", "inventory"],
    optional=["notifications"],
    permissions=["monitoring:manage"],
    nav=[NavEntry(label="Alerts", href="/alerts", icon="siren", section="Operate")],
    widgets=[WidgetDecl(id="monitoring.active-alerts", title="Active alerts")],
    jobs=[JobDecl(id="monitoring.evaluate", title="Evaluate alert rules", default_interval_seconds=60)],
    publishes=[ALERT_FIRING, ALERT_RESOLVED],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=1),
    data_ownership=["alert_rules", "alerts", "maintenance_windows"],
    uninstall_behavior="Disabling stops evaluation; open alerts stay as they were and history is retained.",
    warnings=["Escalation policies and on-call schedules are not implemented yet."],
)

NOTIFICATIONS = ModuleManifest(
    id="notifications",
    version=VERSION,
    name="Notification Channels",
    category=C.INTEGRATION,
    description="Send alert notifications to webhooks (HMAC-signed), ntfy topics and email over SMTP.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=3,
    requires=["core"],
    permissions=["notifications:manage"],
    nav=[NavEntry(label="Notifications", href="/settings/notifications", icon="bell", section="Platform")],
    subscribes=[ALERT_FIRING, ALERT_RESOLVED],
    external_integrations=["HTTP webhooks", "ntfy", "SMTP"],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["notification_channels", "notification_deliveries"],
    warnings=["Matrix, Slack-native and push-provider channels are not implemented yet; a webhook covers most of them."],
)

WAN = ModuleManifest(
    id="wan",
    version=VERSION,
    name="WAN & ISP Operations",
    category=C.NETWORK,
    description="WAN interfaces, gateway health and an opt-in latency and loss probe measured from the Warden host.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=3,
    requires=["core", "inventory"],
    controller_capabilities=["network.wans.read"],
    nav=[NavEntry(label="WAN", href="/wan", icon="globe", section="Network")],
    jobs=[JobDecl(id="wan.probe", title="Probe latency and loss", default_interval_seconds=60)],
    resources=ResourceEstimate(memory_mb=8),
    warnings=[
        "The official API lists WAN interfaces by name only: no link state, addresses, throughput or failover events. "
        "Latency and loss come from Warden's own TCP probe, so they describe the active path from the Warden host. "
        "Speed and bufferbloat tests are not implemented yet."
    ],
)

DCIM = ModuleManifest(
    id="dcim",
    version=VERSION,
    name="Rack & DCIM Manager",
    category=C.INFRASTRUCTURE,
    description="Locations, racks and rack elevations with occupancy validation; rack items link to observed devices for live state.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "inventory"],
    permissions=["dcim:edit"],
    nav=[NavEntry(label="Racks", href="/dcim", icon="rack", section="Infrastructure")],
    resources=ResourceEstimate(memory_mb=16),
    data_ownership=["dcim_locations", "dcim_racks", "dcim_rack_items"],
    uninstall_behavior="Disabling hides racks; documented layouts are retained.",
    warnings=["Rack layouts are documentation. The official API reports no physical location, so placement is entered by hand."],
)

CABLING = ModuleManifest(
    id="cabling",
    version=VERSION,
    name="Cabling & Fiber Documentation",
    category=C.INFRASTRUCTURE,
    description="Cables between device ports, patch panels and outlets; end-to-end path tracing, cable schedule export and label text.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "dcim"],
    permissions=["dcim:edit"],
    nav=[NavEntry(label="Cabling", href="/dcim/cables", icon="cable", section="Infrastructure")],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["dcim_cables", "dcim_cable_ends"],
    warnings=[
        "The official API exposes no LLDP neighbours or uplink port numbers, so cables cannot be discovered; "
        "documented paths are only checked against which device each device reports as its uplink."
    ],
)

POWER = ModuleManifest(
    id="power",
    version=VERSION,
    name="Power & Environment",
    category=C.INFRASTRUCTURE,
    description="Feeds, UPS and PDU chains, load roll-up with A/B failover, headroom, projected load and UPS runtime estimates.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "dcim"],
    permissions=["dcim:edit"],
    nav=[NavEntry(label="Power", href="/dcim/power", icon="plug", section="Infrastructure")],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["dcim_power_feeds", "dcim_power_links"],
    warnings=[
        "Loads are documented typical draw, not measurements: no UPS/PDU integration (SNMP/NUT) exists yet. "
        "The official API reports PoE state but not wattage, so PoE load is an upper bound. "
        "Temperature and humidity sensors are not implemented."
    ],
)

ASSET_LIFECYCLE = ModuleManifest(
    id="asset-lifecycle",
    version=VERSION,
    name="Asset Lifecycle",
    category=C.INFRASTRUCTURE,
    description=(
        "Hardware, spares, licences and subscriptions with purchase data, warranty/support/end-of-life dates and a renewal calendar."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "inventory"],
    permissions=["dcim:edit"],
    nav=[NavEntry(label="Assets", href="/assets", icon="package", section="Infrastructure")],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["assets"],
    warnings=["Licence keys are not stored here; keep them in the credential vault and reference them."],
)

TOPOLOGY = ModuleManifest(
    id="topology",
    version=VERSION,
    name="Network Topology",
    category=C.NETWORK,
    description="Physical uplink tree and logical network view with live health and a VLAN overlay.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "inventory"],
    optional=["monitoring", "dcim"],
    nav=[NavEntry(label="Topology", href="/topology", icon="share", section="Network")],
    resources=ResourceEstimate(memory_mb=8),
    warnings=[
        "Edges come from each device's reported uplink; the official API does not say which port. "
        "The VLAN overlay is inferred from client attachment, not switch port VLAN configuration."
    ],
)

DOCS = ModuleManifest(
    id="docs",
    version=VERSION,
    name="Documentation & Wiki",
    category=C.INFRASTRUCTURE,
    description="Versioned Markdown runbooks and architecture pages with live [[device:…]] inventory references and backlinks.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core"],
    permissions=["docs:edit"],
    nav=[NavEntry(label="Docs", href="/docs", icon="book", section="Infrastructure")],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["doc_pages", "doc_versions"],
    uninstall_behavior="Disabling hides pages; pages and their full history are retained.",
)

SERVERS = ModuleManifest(
    id="servers",
    version=VERSION,
    name="Servers & Virtualization",
    category=C.INFRASTRUCTURE,
    description=(
        "Read-only inventory of Proxmox VE nodes, VMs and containers, Unraid servers (array, Docker, VMs, UPS), "
        "Docker hosts and Linux hosts via node_exporter, matched to the clients the network reports."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=4,
    requires=["core", "inventory"],
    permissions=["servers:manage"],
    nav=[NavEntry(label="Servers", href="/servers", icon="server", section="Infrastructure")],
    jobs=[JobDecl(id="servers.sync", title="Poll servers and hypervisors", default_interval_seconds=60)],
    external_integrations=["Proxmox VE API (8.x/9.x)", "Unraid API (7.2+)", "Docker Engine API 1.47+", "Prometheus node_exporter"],
    resources=ResourceEstimate(memory_mb=32, disk_mb_per_day=5),
    data_ownership=["host_connections", "hosts", "workloads", "host_storage"],
    uninstall_behavior="Disabling stops polling; collected inventory and credentials are retained until connections are deleted.",
    warnings=[
        "All integrations are read-only: Fabric Warden never starts, stops or changes a VM or container. "
        "Docker should be reached through a read-only socket proxy, not an exposed Docker socket. "
        "Guest IP addresses need the QEMU guest agent; container CPU usage is not collected."
    ],
)

FIREWALL = ModuleManifest(
    id="firewall",
    version=VERSION,
    name="Firewall & Policy Manager",
    category=C.SECURITY,
    description="Zone policies in evaluation order with shadowing and redundancy analysis, the zone matrix and hygiene findings.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    controller_capabilities=["network.firewall.policies.read"],
    nav=[NavEntry(label="Firewall", href="/firewall", icon="shield", section="Security")],
    resources=ResourceEstimate(memory_mb=8),
    warnings=[
        "Read-only. Coverage is only claimed when certain from the data; overlapping traffic filters are reported as not determined."
    ],
)

CONFIG_HISTORY = ModuleManifest(
    id="config-history",
    version=VERSION,
    name="Configuration History & Backup",
    category=C.SECURITY,
    description="A snapshot of each site's configuration whenever it changes, semantic diffs, a pinned baseline and drift.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    nav=[NavEntry(label="Config history", href="/config", icon="history", section="Security")],
    publishes=[CONFIG_CHANGED],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=2),
    data_ownership=["config_snapshots"],
    warnings=[
        "The official API keeps no change log, so a diff shows what changed between two syncs, not who changed it. "
        "Snapshots are documentation of observed configuration, not restorable controller backups."
    ],
)

COMPLIANCE = ModuleManifest(
    id="compliance",
    version=VERSION,
    name="Policy & Compliance",
    category=C.SECURITY,
    description=(
        "Built-in controls (MFA, segmentation, firewall hygiene, firmware, drift) evaluated with evidence, audited waivers and CSV export."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    optional=["firewall", "config-history", "servers"],
    permissions=["compliance:manage"],
    nav=[NavEntry(label="Compliance", href="/compliance", icon="clipboard", section="Security")],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["compliance_results"],
    warnings=["Custom baselines are not implemented yet; the built-in controls are fixed."],
)

PLANNED: list[ModuleManifest] = [
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
    _planned("investigations", "Investigation Workbench", C.SECURITY, 5, "Pivot from alerts to clients, flows, logs and changes.", ["soc"]),
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

CATALOG: dict[str, ModuleManifest] = {
    m.id: m
    for m in [
        CORE,
        UNIFI_NETWORK,
        INVENTORY,
        CLIENT_INTEL,
        IPAM,
        ANALYTICS,
        MONITORING,
        NOTIFICATIONS,
        WAN,
        DCIM,
        CABLING,
        POWER,
        ASSET_LIFECYCLE,
        TOPOLOGY,
        DOCS,
        SERVERS,
        FIREWALL,
        CONFIG_HISTORY,
        COMPLIANCE,
        *PLANNED,
    ]
}

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

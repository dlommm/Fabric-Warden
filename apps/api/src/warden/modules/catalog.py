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
        "DHCP leases, LLDP neighbours, IDS/IPS events or historical statistics. Port counters come from the "
        "SNMP Port Counters module and IDS events from Log Management; the rest stay unavailable."
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

SNMP = ModuleManifest(
    id="snmp",
    version=VERSION,
    name="SNMP Port Counters",
    category=C.OBSERVABILITY,
    description=(
        "Read-only IF-MIB polling of switches and gateways: per-port throughput, errors, discards and link state "
        "with history, matched to the devices the controller reports."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    permissions=["snmp:manage"],
    nav=[NavEntry(label="Port counters", href="/snmp", icon="activity", section="Network")],
    jobs=[JobDecl(id="snmp.poll", title="Poll SNMP interface counters", default_interval_seconds=60)],
    external_integrations=["SNMP v2c/v3 agents (IF-MIB, RFC 2863); UniFi: Settings > CyberSecure > Traffic Logging"],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=20),
    data_ownership=["snmp_profiles", "snmp_targets", "snmp_interfaces"],
    uninstall_behavior="Disabling stops polling; interfaces, history and credentials are kept until targets and profiles are deleted.",
    warnings=[
        "Only GET and walk requests are sent; SET is never used. History starts at the first poll. "
        "Matching an interface to a front-panel port is inferred from its name. USW Flex and USW Ultra switches have no SNMP agent. "
        "Optical levels and FEC are not read: UniFi publishes no MIB for them."
    ],
)

CHANGE_ENGINE = ModuleManifest(
    id="change-engine",
    version=VERSION,
    name="Change Approval & Recovery",
    category=C.AUTOMATION,
    description=(
        "The single path for controller writes: changes are proposed with the live state, a diff, risk and impact, approved, "
        "applied only after a fresh re-read, verified by re-reading the controller, and reverted when a reversible check fails."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=6,
    requires=["core", "unifi-network", "inventory"],
    permissions=["changes:propose", "changes:approve"],
    controller_capabilities=["network.info.read"],
    nav=[NavEntry(label="Changes", href="/changes", icon="git-pull-request", section="Operate")],
    jobs=[JobDecl(id="changes.verify", title="Verify applied changes and expire stale proposals", default_interval_seconds=30)],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["change_requests"],
    uninstall_behavior=(
        "Disabling stops all writes; change history is kept. Controllers stay in their current write mode until an owner changes it."
    ),
    warnings=[
        "Operations today: turn firewall policy logging on or off, restart a device, power-cycle a PoE port. "
        "Each controller is read-only until an owner allows changes on it. Only logging changes have an automatic revert; "
        "restarts and PoE cycles cannot be undone and are verified by watching the device or port come back."
    ],
)

BULK_OPS = ModuleManifest(
    id="bulk-ops",
    version=VERSION,
    name="Bulk Operations",
    category=C.AUTOMATION,
    description=(
        "One change over many targets: PoE-cycle several ports, restart several devices, or switch logging, policies, "
        "internet access or isolation for several objects. Proposed and approved once; applied one item at a time through "
        "the change engine, each re-read, verified and evidenced; stops at the first item that does not succeed."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=6,
    requires=["core", "unifi-network", "inventory", "change-engine"],
    permissions=["changes:propose", "changes:approve"],
    nav=[NavEntry(label="Bulk operations", href="/changes/batches", icon="layers", section="Operate")],
    jobs=[JobDecl(id="bulk-ops.advance", title="Continue running bulk operations", default_interval_seconds=30)],
    publishes=["change.batch.proposed", "change.batch.decided", "change.batch.finished"],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["change_batches"],
    uninstall_behavior="Disabling stops running batches from advancing; batches and their changes are kept.",
    warnings=[
        "Adds no write path: every item is an ordinary change request. Up to 50 targets per batch. "
        "Tested only against the simulated controller."
    ],
)

AUTOMATION = ModuleManifest(
    id="automation",
    version=VERSION,
    name="Automation Studio",
    category=C.AUTOMATION,
    description=(
        "Rules: an event or a schedule, conditions, and one action: propose a change (which still waits for a person to "
        "approve and apply it) or send a notification. Dry runs replay recent events; every firing is kept as run history."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=6,
    requires=["core", "inventory", "change-engine"],
    permissions=["changes:propose"],
    nav=[NavEntry(label="Automation", href="/automation", icon="workflow", section="Operate")],
    jobs=[JobDecl(id="automation.evaluate", title="Evaluate automation rules against new events", default_interval_seconds=30)],
    subscribes=[
        ALERT_FIRING,
        ALERT_RESOLVED,
        DEVICE_STATE_CHANGED,
        DEVICE_MISSING,
        DEVICE_DISCOVERED,
        DEVICE_FIRMWARE_CHANGED,
        CLIENT_CONNECTED,
        CLIENT_DISCONNECTED,
        CLIENT_MOVED,
        CONTROLLER_STATUS_CHANGED,
        CONFIG_CHANGED,
    ],
    resources=ResourceEstimate(memory_mb=8),
    data_ownership=["automation_rules", "automation_runs"],
    uninstall_behavior="Disabling stops rules from firing; rules and run history are kept. Re-enabled rules do not replay missed events.",
    warnings=[
        "A rule never approves or applies a change. Notifications need the Notifications module. "
        "Multi-step workflows and approval routing are not built."
    ],
)

DIGITAL_TWIN = ModuleManifest(
    id="digital-twin",
    version=VERSION,
    name="Network Digital Twin",
    category=C.AUTOMATION,
    description=(
        "Impact preview for proposed changes and bulk operations: the devices, clients, networks, zones and policies a change "
        "would touch, computed from the observed inventory graph (uplink tree, subnets, zone and policy matrix)."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=6,
    requires=["core", "inventory", "change-engine"],
    permissions=["inventory:read"],
    resources=ResourceEstimate(memory_mb=8),
    uninstall_behavior="Read-only; stores nothing.",
    warnings=[
        "Impact preview only. Versioned graph snapshots and what-if simulation are not built yet. The preview uses the last "
        "synced inventory and cannot name the device on a switch port (the official API does not report it)."
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

LOGS = ModuleManifest(
    id="logs",
    version=VERSION,
    name="Log Management",
    category=C.SECURITY,
    description="Syslog receiver (RFC 3164, RFC 5424, CEF) with search, categories, per-source volume and retention.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    nav=[NavEntry(label="Logs", href="/logs", icon="scroll", section="Security")],
    jobs=[JobDecl(id="logs.retention", title="Expire old log events", default_interval_seconds=3600)],
    resources=ResourceEstimate(memory_mb=64, disk_mb_per_day=200),
    data_ownership=["log_events"],
    warnings=[
        "The receiver listens only when WARDEN_SYSLOG_PORT is set (UDP). Categories are a keyword heuristic, not a parser per vendor; "
        "UniFi's CEF field set is not specially mapped because it has not been verified against a published reference."
    ],
)

SOC = ModuleManifest(
    id="soc",
    version=VERSION,
    name="Security Operations Center",
    category=C.SECURITY,
    description="One queue of security signals from firewall analysis, compliance, alerts and logs, with cases to work them.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    optional=["firewall", "compliance", "monitoring", "logs"],
    permissions=["soc:manage"],
    nav=[NavEntry(label="SOC", href="/soc", icon="siren", section="Security")],
    resources=ResourceEstimate(memory_mb=16),
    data_ownership=["soc_cases"],
    warnings=["IDS/IPS events are only available through syslog; the official UniFi API does not expose them."],
)

INVESTIGATIONS = ModuleManifest(
    id="investigations",
    version=VERSION,
    name="Investigation Workbench",
    category=C.SECURITY,
    description="Pivot from an IP, MAC or name to every client, device, server, log line, alert, change and case that mentions it.",
    maturity=Maturity.AVAILABLE,
    roadmap_phase=5,
    requires=["core", "inventory"],
    optional=["client-intel", "logs", "servers", "soc"],
    nav=[NavEntry(label="Investigate", href="/investigate", icon="search", section="Security")],
    resources=ResourceEstimate(memory_mb=8),
)


UNIFI_PROTECT = ModuleManifest(
    id="unifi-protect",
    version=VERSION,
    name="UniFi Protect",
    category=C.INTEGRATION,
    description=(
        "Read-only cameras and NVR from the official Protect Integration API, matched to network clients by MAC, "
        "with the uplink switch and, where a cable is documented, the switch port and its PoE state."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=9,
    requires=["core", "inventory"],
    optional=["cabling"],
    permissions=["integrations:manage"],
    nav=[NavEntry(label="Cameras", href="/protect", icon="cctv", section="Infrastructure")],
    jobs=[JobDecl(id="unifi-protect.sync", title="Poll UniFi Protect", default_interval_seconds=60)],
    external_integrations=["UniFi Protect Integration API (5.3.38 to 7.3.70 specs)"],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=1),
    data_ownership=["integration_connections", "protect_devices"],
    uninstall_behavior="Disabling stops polling; cameras and the API key are retained until the connection is deleted.",
    warnings=[
        "Read-only: only GET meta/info, cameras and nvrs are called. The published API reports no camera IP, "
        "NVR storage or disk health, so those are not shown.",
    ],
)

HOME_ASSISTANT = ModuleManifest(
    id="home-assistant",
    version=VERSION,
    name="Home Assistant",
    category=C.INTEGRATION,
    description=(
        "Read-only UPS and temperature sensors, connectivity sensors and router-based device trackers from the "
        "Home Assistant REST API; trackers are matched to network clients."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=9,
    requires=["core", "inventory"],
    permissions=["integrations:manage"],
    nav=[NavEntry(label="Home Assistant", href="/home-assistant", icon="home", section="Infrastructure")],
    jobs=[JobDecl(id="home-assistant.sync", title="Poll Home Assistant", default_interval_seconds=60)],
    external_integrations=["Home Assistant REST API (GET /api/, /api/config, /api/states)"],
    resources=ResourceEstimate(memory_mb=16, disk_mb_per_day=1),
    data_ownership=["integration_connections", "ha_entities"],
    uninstall_behavior="Disabling stops polling; entities and the token are retained until the connection is deleted.",
    warnings=[
        "Read-only: no actions or services are called. Only selected entities and allow-listed attributes are kept; "
        "GPS trackers are skipped, so no location is stored.",
    ],
)

MCP = ModuleManifest(
    id="mcp",
    version=VERSION,
    name="MCP Gateway",
    category=C.AI,
    description=(
        "A Model Context Protocol endpoint (/api/mcp, Streamable HTTP) with read-only tools over inventory, clients, "
        "alerts, firewall findings, cameras and Home Assistant, authenticated with API tokens."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=9,
    requires=["core", "inventory"],
    optional=["monitoring", "firewall", "servers", "unifi-protect", "home-assistant"],
    permissions=["mcp:use"],
    resources=ResourceEstimate(memory_mb=8),
    uninstall_behavior="Disabling makes /api/mcp answer 409; nothing is stored apart from audit records of tool calls.",
    warnings=[
        "Tools are read-only. A token needs mcp:use plus each tool's read permission; narrowed tokens see fewer tools. "
        "Write tools (change proposals through the change engine) and the Paperclip gateway are planned, not built.",
    ],
)

AI_PROVIDERS = ModuleManifest(
    id="ai-providers",
    version=VERSION,
    name="AI Providers",
    category=C.AI,
    description=(
        "Connections to local model servers (Ollama, LM Studio and other OpenAI-compatible endpoints) and to Anthropic Claude "
        "through the official Messages API: connection test, model listing, token and cost usage, monthly budgets."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=7,
    requires=["core"],
    permissions=["ai:manage", "ai:use"],
    nav=[NavEntry(label="AI Providers", href="/ai/providers", icon="cpu", section="AI")],
    external_integrations=["Anthropic Messages API (2023-06-01)", "OpenAI-compatible chat completions", "Ollama API"],
    resources=ResourceEstimate(memory_mb=16),
    data_ownership=["ai_providers", "ai_usage"],
    uninstall_behavior="Disabling stops every model call; providers, keys (in the vault) and usage history are kept.",
    warnings=[
        "Optional: nothing else in Fabric Warden needs a model. A local provider keeps all data on your network; an API "
        "provider receives the agent's prompt and the tool results it reads (inventory, metrics, logs, firewall policies)."
    ],
)

AI_AGENTS = ModuleManifest(
    id="ai-agents",
    version=VERSION,
    name="AI Agent Center",
    category=C.AI,
    description=(
        "Specialised read-only network agents (explain an alert, why a client is slow, firewall review) that answer from "
        "Fabric Warden's own data through a fixed, permission-scoped tool set, with autonomy levels, budgets, kill switches "
        "and every run kept as evidence."
    ),
    maturity=Maturity.AVAILABLE,
    roadmap_phase=7,
    requires=["core", "inventory", "ai-providers"],
    optional=["monitoring", "firewall", "logs", "change-engine"],
    permissions=["ai:use", "ai:manage"],
    nav=[NavEntry(label="Agent Center", href="/ai/agents", icon="sparkles", section="AI")],
    resources=ResourceEstimate(memory_mb=32),
    data_ownership=["ai_agent_configs", "ai_agent_runs"],
    uninstall_behavior="Disabling stops agents from running; past runs and their evidence are kept.",
    warnings=[
        "The highest autonomy in this release is “propose”: an agent can put a change into the Change Approval queue and "
        "nothing more. People approve and apply. Answers are AI-generated and labelled as such."
    ],
)

PLANNED: list[ModuleManifest] = [
    _planned("ndr", "Network Detection & Response", C.SECURITY, 5, "Explainable flow-based anomaly detection.", ["flows"]),
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
        SNMP,
        CHANGE_ENGINE,
        BULK_OPS,
        AUTOMATION,
        DIGITAL_TWIN,
        FIREWALL,
        CONFIG_HISTORY,
        COMPLIANCE,
        LOGS,
        SOC,
        INVESTIGATIONS,
        UNIFI_PROTECT,
        HOME_ASSISTANT,
        MCP,
        AI_PROVIDERS,
        AI_AGENTS,
        *PLANNED,
    ]
}

PRESETS: dict[str, dict[str, object]] = {
    "minimal": {"name": "Minimal Core", "modules": ["core", "unifi-network", "inventory"]},
    "monitoring": {
        "name": "Network Monitoring",
        "modules": ["core", "unifi-network", "inventory", "monitoring", "analytics-studio", "wan", "notifications", "topology", "snmp"],
    },
    "administrator": {
        "name": "Network Administrator",
        "modules": [
            "core",
            "unifi-network",
            "inventory",
            "ipam",
            "snmp",
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

# Module architecture

Every module is declared by a `ModuleManifest` (`apps/api/src/warden/modules/contracts.py`) in the bundled catalog (`catalog.py`): id, version, name, category, description, maturity, required/optional dependencies, controller capabilities, permissions, nav entries, widgets, jobs, published/subscribed events, external integrations, resource estimate, config schema, data ownership, whether it writes, uninstall behaviour.

## Lifecycle (`modules/service.py`)

- **Enable** resolves the transitive dependency closure. Missing deps → 409 listing them, unless `with_dependencies=true`. A module that is not available cannot be enabled.
- **Disable** refuses while enabled dependents exist unless `cascade=true` (dependents are disabled leaves-first). `always_enabled` modules (core) can't be disabled.
- Disabled modules: their jobs are skipped by the scheduler, their API routers return 409 (`require_module`), and their data is **retained**.
- **Presets** enable every module they list (with dependencies); they never disable anything.
- Every change is audited and published as `module.enabled` / `module.disabled`.

## Bundled modules

From `apps/api/src/warden/modules/catalog.py`. `core` is always on.

| Module | Name |
|---|---|
| `core` | Platform Core |
| `unifi-network` | UniFi Network Integration |
| `inventory` | Asset & Device Inventory |
| `client-intel` | Client Intelligence |
| `ipam` | IP Address Management |
| `analytics-studio` | Analytics Studio |
| `monitoring` | Monitoring & Alerts |
| `notifications` | Notification Channels |
| `wan` | WAN & ISP Operations |
| `dcim` | Rack & DCIM Manager |
| `cabling` | Cabling & Fiber Documentation |
| `power` | Power & Environment |
| `asset-lifecycle` | Asset Lifecycle |
| `topology` | Network Topology |
| `docs` | Documentation & Wiki |
| `servers` | Servers & Virtualization |
| `snmp` | SNMP Port Counters |
| `change-engine` | Change Approval & Recovery |
| `bulk-ops` | Bulk Operations |
| `automation` | Automation Studio |
| `digital-twin` | Network Digital Twin |
| `firewall` | Firewall & Policy Manager |
| `config-history` | Configuration History & Backup |
| `compliance` | Policy & Compliance |
| `logs` | Log Management |
| `flows` | Traffic & Flow Analytics |
| `soc` | Security Operations Center |
| `investigations` | Investigation Workbench |
| `unifi-protect` | UniFi Protect |
| `home-assistant` | Home Assistant |
| `mcp` | MCP Gateway |
| `ai-providers` | AI Providers |
| `ai-agents` | AI Agent Center |

Controller writes happen only through `change-engine` (bulk operations, automation and AI agents create ordinary change requests through it); every other module is read-only towards the controller.


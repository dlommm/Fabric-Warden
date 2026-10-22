# Module architecture

Every module is declared by a `ModuleManifest` (`apps/api/src/warden/modules/contracts.py`) in the bundled catalog (`catalog.py`): id, version, name, category, description, maturity (`available` | `preview` | `planned`), roadmap phase, required/optional dependencies, controller capabilities, permissions, nav entries, widgets, jobs, published/subscribed events, external integrations, resource estimate, config schema, data ownership, whether it writes, uninstall behaviour.

## Lifecycle (`modules/service.py`)

- **Enable** resolves the transitive dependency closure. Missing deps → 409 listing them, unless `with_dependencies=true`. Any `planned` module in the closure blocks enabling.
- **Disable** refuses while enabled dependents exist unless `cascade=true` (dependents are disabled leaves-first). `always_enabled` modules (core) can't be disabled.
- Disabled modules: their jobs are skipped by the scheduler, their API routers return 409 (`require_module`), and their data is **retained**.
- **Presets** enable every available module they list (with dependencies) and report planned ones as unavailable; they never disable anything.
- Every change is audited and published as `module.enabled` / `module.disabled`.

## Shipping today

Generated from the bundled catalog (`apps/api/src/warden/modules/catalog.py`) for 1.0.0-rc.1: 32 modules are available and 5 are planned (visible in the Module Center, cannot be enabled). `core` is always on. "Available" means built and tested against the simulators; see [CHANGELOG.md](CHANGELOG.md) for what each one has not yet been validated against (live controllers, real hosts, real model servers).

| Module | Name | Maturity | Roadmap phase |
|---|---|---|---|
| `core` | Platform Core | available | 1 |
| `unifi-network` | UniFi Network Integration | available | 1 |
| `inventory` | Asset & Device Inventory | available | 1 |
| `client-intel` | Client Intelligence | available | 2 |
| `ipam` | IP Address Management | available | 2 |
| `analytics-studio` | Analytics Studio | available | 3 |
| `monitoring` | Monitoring & Alerts | available | 3 |
| `notifications` | Notification Channels | available | 3 |
| `wan` | WAN & ISP Operations | available | 3 |
| `dcim` | Rack & DCIM Manager | available | 4 |
| `cabling` | Cabling & Fiber Documentation | available | 4 |
| `power` | Power & Environment | available | 4 |
| `asset-lifecycle` | Asset Lifecycle | available | 4 |
| `topology` | Network Topology | available | 4 |
| `docs` | Documentation & Wiki | available | 4 |
| `servers` | Servers & Virtualization | available | 4 |
| `snmp` | SNMP Port Counters | available | 5 |
| `change-engine` | Change Approval & Recovery | available | 6 |
| `bulk-ops` | Bulk Operations | available | 6 |
| `automation` | Automation Studio | available | 6 |
| `digital-twin` | Network Digital Twin | available | 6 |
| `firewall` | Firewall & Policy Manager | available | 5 |
| `config-history` | Configuration History & Backup | available | 5 |
| `compliance` | Policy & Compliance | available | 5 |
| `logs` | Log Management | available | 5 |
| `soc` | Security Operations Center | available | 5 |
| `investigations` | Investigation Workbench | available | 5 |
| `unifi-protect` | UniFi Protect | available | 9 |
| `home-assistant` | Home Assistant | available | 9 |
| `mcp` | MCP Gateway | available | 9 |
| `ai-providers` | AI Providers | available | 7 |
| `ai-agents` | AI Agent Center | available | 7 |
| `ndr` | Network Detection & Response | planned | 5 |
| `switching` | Switch Operations | planned | 3 |
| `wifi` | Wi-Fi Operations | planned | 3 |
| `fiber` | Fiber & High-Speed Diagnostics | planned | 3 |
| `flows` | Traffic & Flow Analytics | planned | 5 |

Controller writes happen only through `change-engine` (bulk operations, automation and AI agents create ordinary change requests through it); every other module is read-only towards the controller.

Third-party modules: not supported yet. Planned direction: versioned SDK, signed packages, explicit privilege declarations, and execution outside the privileged core process.

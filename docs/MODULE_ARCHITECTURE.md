# Module architecture

Every module is declared by a `ModuleManifest` (`apps/api/src/warden/modules/contracts.py`) in the bundled catalog (`catalog.py`): id, version, name, category, description, maturity (`available` | `preview` | `planned`), roadmap phase, required/optional dependencies, controller capabilities, permissions, nav entries, widgets, jobs, published/subscribed events, external integrations, resource estimate, config schema, data ownership, whether it writes, uninstall behaviour.

## Lifecycle (`modules/service.py`)

- **Enable** resolves the transitive dependency closure. Missing deps → 409 listing them, unless `with_dependencies=true`. Any `planned` module in the closure blocks enabling.
- **Disable** refuses while enabled dependents exist unless `cascade=true` (dependents are disabled leaves-first). `always_enabled` modules (core) can't be disabled.
- Disabled modules: their jobs are skipped by the scheduler, their API routers return 409 (`require_module`), and their data is **retained**.
- **Presets** enable every available module they list (with dependencies) and report planned ones as unavailable; they never disable anything.
- Every change is audited and published as `module.enabled` / `module.disabled`.

## Shipping today

| Module | Maturity |
|---|---|
| `core` | available, always on |
| `unifi-network` | available |
| `inventory` | available |
| 33 others | planned (see Module Center or `catalog.py`) |

Third-party modules: not supported yet. Planned direction: versioned SDK, signed packages, explicit privilege declarations, and execution outside the privileged core process. See [MODULE_SDK.md](MODULE_SDK.md).

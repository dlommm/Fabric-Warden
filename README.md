# Fabric Warden

Self-hosted, modular network operations for UniFi: inventory, analytics, configuration history, security investigation, rack/DCIM documentation and optional AI agents in one web interface. The UniFi controller stays the authoritative control plane; Fabric Warden reads it through the **official** Integration API and is **read-only by default**.

> **Status: Phases 1 (core platform), 2 (inventory and IPAM) and 3 (analytics and alerting) implemented.** Most modules in the catalog are declared but *planned*; the Module Center shows exactly which. See [docs/ROADMAP.md](docs/ROADMAP.md).

## What works today

- First-run setup: owner account → controller (real console or built-in simulator) → module preset
- Sign-in with optional TOTP MFA, sessions, API tokens narrowed to chosen permissions, change password
- Five built-in roles (owner, admin, operator, auditor, viewer)
- Credential vault (Fernet encryption at rest) for controller API keys and TOTP secrets
- Module Center: enable/disable with dependency resolution, presets, planned modules visible but locked
- UniFi Network connection via API key, per-version capability detection from Ubiquiti's published OpenAPI specs, probing of read endpoints
- Inventory sync: sites, devices (ports, radios, latest stats), clients, networks/VLANs, WANs, firewall zones and policies — every record carries source, freshness and demo labelling
- Event bus with persisted history (discovered, state/firmware changes, missing devices, client connect/disconnect), live updates over SSE
- Hash-chained, tamper-evident audit trail
- Client Intelligence: Client 360 with route, address/attachment history and user-owned identity
- IPAM: documented prefixes, reservations and VLANs checked against the network (conflicts, duplicates, VLAN mismatches, pool overlaps), with a keyboard-operable address map; device asset records
- Analytics: metric history sampled on every sync (raw, 5-minute and hourly tiers), dashboards with templates, device history charts
- Alerts: threshold rules with hold times, acknowledgement and maintenance windows; notifications to webhooks (HMAC-signed), ntfy and email
- WAN: interfaces, gateway health and an opt-in latency/loss probe
- Web UI: customizable Overview (exceptions first), devices, device detail with upstream route, clients, networks + zones, controllers + capability matrix, Module Center, audit, users, account; light/dark, comfortable/compact
- Deterministic simulated UniFi controller for demos and tests (always labelled *Demo data*)

## What it deliberately does not do (yet)

- **No controller writes.** The client refuses non-GET requests unless the (not yet built) change engine issues a write authorization.
- No SFP optics/FEC, port error counters, DHCP leases, controller fixed-IP reservations, client fingerprints, LLDP or IDS events: the official API does not expose them. See [docs/API_CAPABILITIES.md](docs/API_CAPABILITIES.md).
- No AI features yet (Phase 7+).

## Quick start (development)

```bash
# API (SQLite by default)
cd apps/api && uv sync && WARDEN_COOKIE_SECURE=false uv run warden    # http://localhost:8000/api/docs

# Web
cd apps/web && pnpm install && pnpm dev                                 # http://localhost:3000
```

Open http://localhost:3000, create the owner and choose **Use the demo controller** to explore without hardware.

## Deploy

Docker Compose with PostgreSQL and Redis: see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Repository layout

```
apps/api      FastAPI backend (package `warden`): core, modules, UniFi integration, simulator, migrations, tests
apps/web      Next.js frontend
deploy/       Docker Compose
docs/         Architecture, security model, API capabilities, roadmap
PRODUCT.md    Product context; DESIGN.md: design system
```

## Tests

```bash
cd apps/api && uv run pytest     # 61 tests: auth, RBAC matrix, modules, UniFi capability gating, sync, audit chain, migrations
cd apps/web && npx tsc --noEmit && pnpm lint && pnpm build
```

# Fabric Warden

Self-hosted, modular network operations for UniFi: inventory, analytics, configuration history, security investigation, rack/DCIM documentation and optional AI agents in one web interface. The UniFi controller stays the authoritative control plane; Fabric Warden reads it through the **official** Integration API and is **read-only by default**.

> **Status: release candidate 1.0.0-rc.1.** Phases 1–7 and 9 are implemented and Phase 10 hardening is done; everything has been tested against the built-in simulators; read-only sync has also been checked against one live UniFi console, and no change has yet been written to a live one. Five catalog modules remain *planned*; the Module Center shows exactly which. See [docs/CHANGELOG.md](docs/CHANGELOG.md).

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
- Infrastructure: locations and racks with front/rear elevations and occupancy checks; cables traced through patch panels with schedules and labels; power chains with failover headroom and UPS runtime estimates; asset lifecycle with a renewal calendar; topology with a VLAN overlay; a versioned wiki whose `[[device:…]]` references stay live. See [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md)
- Servers: read-only Proxmox VE, Unraid, Docker and node_exporter inventory, with each host and VM matched to the network client it is. See [docs/SERVERS.md](docs/SERVERS.md)
- Web UI: customizable Overview (exceptions first), devices, device detail with upstream route, clients, networks + zones, controllers + capability matrix, Module Center, audit, users, account; light/dark, comfortable/compact
- Security and configuration: firewall policy analysis, configuration history, compliance, syslog log management, SOC signals and cases, investigation workbench, SNMP port counters
- Changes: a single audited change engine (propose with live state, approve, re-read, apply, verify, revert where possible), bulk operations, automation rules that can only propose, digital-twin impact preview. Every controller starts read-only
- Ecosystem and AI (optional): UniFi Protect and Home Assistant read-only, an MCP server with read-only tools, AI providers and read-only agents that can at most propose a change
- Operations: encrypted owner-only backup and restore (API and `warden-backup` CLI), health checks, hardened non-root containers. See [docs/OPERATIONS.md](docs/OPERATIONS.md)
- Deterministic simulated UniFi controller for demos and tests (always labelled *Demo data*)

## What it deliberately does not do (yet)

- **No writes outside the change engine.** The client refuses non-GET requests unless the change engine issues a write authorization for an approved change; only operations in the published API spec are offered.
- No SFP optics/FEC, port error counters, DHCP leases, controller fixed-IP reservations, client fingerprints, LLDP or IDS events: the official API does not expose them. See [docs/API_CAPABILITIES.md](docs/API_CAPABILITIES.md).
- No OIDC/SSO sign-in, site-scoped roles, backup scheduler or Phase 8 advanced AI yet.

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
docs/         Architecture, security model, API capabilities, operations
```

## Tests

```bash
cd apps/api && uv run pytest && uv run ruff check src tests   # unit/integration tests against SQLite and the simulators
cd apps/web && npx tsc --noEmit && pnpm lint && pnpm build
cd apps/web && pnpm e2e                                         # browser E2E (Playwright, headless, offline)
```

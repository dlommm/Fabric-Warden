# Fabric Warden

Self-hosted, modular network operations for UniFi: inventory, analytics, configuration history, security investigation, rack and cabling documentation, and optional AI agents in one web interface.

Your UniFi controller stays in charge. Fabric Warden reads it through Ubiquiti's **official** Integration API and is **read-only by default**. Any change goes through a single audited change engine that you approve, and every controller starts with writes switched off.


## Features

**Network**
- Inventory sync: sites, devices (ports, radios, stats), clients, networks/VLANs, WANs, firewall zones and policies. Every record shows its source and freshness.
- Client 360: route to the client, address and attachment history, and identity you own.
- IPAM: documented prefixes, reservations and VLANs checked against the live network (conflicts, duplicates, VLAN mismatches, pool overlaps).
- SNMP port counters (read-only IF-MIB): throughput, utilisation, errors and discards per port.
- NetFlow v5/v9 and IPFIX collector: top talkers, conversations and service ports, matched to clients and devices.
- WAN health, with an opt-in latency and loss probe.

**Monitoring and security**
- Metric history, dashboards and device charts.
- Alerts with hold times, acknowledgement and maintenance windows. Notifications go to webhooks (HMAC-signed), ntfy and email.
- Firewall policy analysis (shadowed, redundant and permissive rules), configuration history with drift detection, and compliance checks.
- Syslog log management, SOC signals and cases, and an investigation workbench.

**Changes, safely**
- One change engine. Each change is proposed with live state, approved, re-read, applied and verified, then reverted where possible. Evidence is kept for every step.
- Bulk operations, automation rules that can only *propose*, and a digital-twin impact preview.
- Gateway restarts, default networks and the controller's own network are protected paths that need explicit acknowledgement.

**Infrastructure**
- Locations and racks with front and rear elevations, cables traced through patch panels with suggestions from observed uplinks and drift events for automation, power chains with UPS runtime, asset lifecycle, and topology with a VLAN overlay.
- A versioned wiki whose `[[device:…]]` references stay live.
- Read-only Proxmox VE, Unraid, Docker and node_exporter inventory, with each host matched to its network client.

**Ecosystem and AI (optional, off by default)**
- UniFi Protect and Home Assistant (read-only), and an MCP server with read-only tools.
- AI providers (Ollama, OpenAI-compatible, Anthropic) with budgets and kill switches. Agents explain by default, and at most propose a change for a person to approve.

**Platform**
- Owner, admin, operator, auditor and viewer roles. TOTP MFA, scoped API tokens, and an encrypted credential vault.
- A hash-chained, tamper-evident audit trail.
- A Module Center: turn features on and off, with dependency resolution and presets.
- Encrypted backup and restore, account recovery from the server shell, and hardened non-root containers.
- A deterministic simulated controller for demos and tests (always labelled *Demo data*).

## What it does not do

- **No writes outside the change engine**, and only operations in Ubiquiti's published API are offered.
- Nothing the official API doesn't expose: no DHCP leases, LLDP, IDS events, SFP optics or client fingerprints. See [API capabilities](docs/API_CAPABILITIES.md).

## Quick start (Docker)

You need Docker with Compose. A UniFi console and an API key are optional, because the built-in demo controller works without hardware.

```bash
git clone https://github.com/dlommm/Fabric-Warden.git && cd Fabric-Warden
cp deploy/.env.example deploy/.env    # set POSTGRES_PASSWORD and WARDEN_SECRET_KEY (openssl rand -base64 32)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Or run the published image on its own, with SQLite in a volume:

```bash
docker run -d --name fabric-warden --restart unless-stopped -p 8080:3000 \
  -v fabric-warden-data:/data -e WARDEN_COOKIE_SECURE=false dendlomm/fabric-warden:latest
```

`WARDEN_COOKIE_SECURE=false` is for plain HTTP on a trusted LAN; behind a TLS proxy leave it out. Images are on Docker Hub as [`dendlomm/fabric-warden`](https://hub.docker.com/r/dendlomm/fabric-warden) for `linux/amd64` and `linux/arm64`, tagged `latest` and by version.

On Unraid, use `deploy/unraid/docker-compose.yml` with the Compose Manager plugin ([DEPLOYMENT.md](docs/DEPLOYMENT.md#unraid)).

Open `http://<host>:8080` and create the owner account. Then add your controller with an API key (UniFi Network → Settings → Control Plane → Integrations), or choose **Use the demo controller**.

For TLS, reverse proxies, backups and upgrades, see [Deployment](docs/DEPLOYMENT.md) and [Operations](docs/OPERATIONS.md).

## Development

You need Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node.js 20+ with [pnpm](https://pnpm.io/).

```bash
cd apps/api && uv sync && WARDEN_COOKIE_SECURE=false uv run warden    # API on :8000, SQLite by default
cd apps/web && pnpm install && pnpm dev                                 # web on :3000, proxies /api
```

Tests:

```bash
cd apps/api && uv run pytest && uv run ruff check src tests
cd apps/web && npx tsc --noEmit && pnpm lint && pnpm build
cd apps/web && pnpm e2e        # browser E2E against the simulator (Playwright)
```

## Documentation

| Topic | Documents |
|---|---|
| How it is built | [Architecture](docs/ARCHITECTURE.md) · [Modules](docs/MODULE_ARCHITECTURE.md) · [Database](docs/DATABASE.md) |
| Running it | [Deployment](docs/DEPLOYMENT.md) · [Operations](docs/OPERATIONS.md) · [Security configuration](docs/SECURITY_CONFIG.md) |
| Making changes | [Change engine](docs/CHANGE_ENGINE.md) · [Rollback and recovery](docs/ROLLBACK_AND_RECOVERY.md) |
| UniFi API coverage | [API capabilities](docs/API_CAPABILITIES.md) |
| Features | [IPAM](docs/IPAM.md) · [Analytics](docs/ANALYTICS.md) · [Infrastructure](docs/INFRASTRUCTURE.md) · [Servers](docs/SERVERS.md) |
| Integrations | [Protect](docs/PROTECT.md) · [Home Assistant](docs/HOME_ASSISTANT.md) · [MCP](docs/MCP.md) |
| AI | [Architecture](docs/AI_ARCHITECTURE.md) · [Providers](docs/AI_PROVIDER_SETUP.md) · [Agents](docs/AGENT_CATALOG.md) · [Autonomy](docs/AGENT_AUTONOMY.md) |
| Reference | [Security model](docs/SECURITY_MODEL.md) · [Testing](docs/TESTING.md) · [Changelog](docs/CHANGELOG.md) |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a public issue.

## License

[GNU Affero General Public License v3.0 or later](LICENSE). If you run a modified version for others over a network, you must offer them its source.

Fabric Warden is an independent project, not affiliated with or endorsed by Ubiquiti Inc. UniFi is a trademark of Ubiquiti Inc.

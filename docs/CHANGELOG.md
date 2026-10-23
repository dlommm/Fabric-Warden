# Changelog

## [1.1.0](https://github.com/dlommm/Fabric-Warden/compare/v1.0.0...v1.1.0) (2026-10-23)


### Added

* **cabling:** suggest missing cables and publish cabling drift as automation triggers ([9abf3ae](https://github.com/dlommm/Fabric-Warden/commit/9abf3aeebfa56f30a6932e6cf05cbf689f2528ea))
* **docker:** ship the API and web app as one image ([9d28f67](https://github.com/dlommm/Fabric-Warden/commit/9d28f6785be595efe8602308d7ad01d65fe4755f))
* **web:** redesign the web UI as a live network schematic ([659b257](https://github.com/dlommm/Fabric-Warden/commit/659b2577af71d8ee14931d2e412e2b1245834748))

## 1.0.0

First public version.

### Network
- Read-only UniFi Network sync through the official Integration API, with per-version capability detection from Ubiquiti's published OpenAPI specs: sites, devices (ports, radios, stats), clients, networks/VLANs, WANs, firewall zones and policies.
- Client Intelligence: Client 360, address and attachment history, client identity.
- IPAM: prefixes, reservations and VLAN records checked against the network; address map; device asset records.
- SNMP port counters (v3/v2c, IF-MIB): per-port throughput, utilisation, errors and discards, with a built-in "Port errors" alert rule. Targets added by address attach to their inventory device automatically, including gateways listed under their WAN address.
- WAN page with an opt-in latency and loss probe.
- Gateways are recognised by model when the console omits the `gateway` feature, so gateway restarts are always high-risk and protected.

### Monitoring and security
- Metric history with rollups, dashboards and device charts.
- Alerts with hold times, acknowledgement and maintenance windows; webhook (HMAC-signed), ntfy and email notifications.
- Firewall policy analysis (shadowing, redundancy, zone matrix), configuration history with semantic diffs and drift, and 11 compliance controls with waivers and evidence export.
- Syslog receiver (RFC 3164/5424/CEF) with search and retention, SOC signals and cases, investigation workbench.

### Changes
- A single change engine: proposals with live state, diff, risk and protected paths; approval with an optional second person; per-controller write mode (read-only by default, owner only); conflict detection; verification by re-reading; automatic revert; evidence and audit.
- Operations: firewall policy logging and enable/disable, device restart, PoE power-cycle, network rename, internet access, isolation and zone moves, custom zone rename. System-defined and derived ("Return") firewall policies are never changed.
- Bulk operations, automation rules whose only action is a proposal, and a digital-twin impact preview.

### Infrastructure
- Racks and locations with elevations, cabling traced through patch panels, power chains with UPS runtime, asset lifecycle, topology with VLAN overlay, and a versioned wiki with live device references.
- Read-only Proxmox VE, Unraid, Docker and node_exporter inventory matched to network clients.

### Ecosystem and AI
- UniFi Protect and Home Assistant (read-only), and an MCP server with read-only tools.
- AI providers (Ollama, OpenAI-compatible, Anthropic) with vault-stored keys, usage, cost and budgets. Eight agents and "Ask the network" over read-only tools, global and per-agent kill switches, and autonomy up to "act after approval", where a person's approval applies the proposal through the change engine.

### Platform and security
- Owner, admin, operator, auditor and viewer roles; TOTP MFA; scoped API tokens; Fernet credential vault; hash-chained audit trail including authorization denials.
- CSRF protection on every cookie write and sign-in, layered sign-in limits, an SSRF guard on every user-supplied address, 2 MiB request limit, secret redaction in logs and audit, and security headers on the API and web app.
- Encrypted backup and restore (`warden-backup`), account recovery (`warden-admin reset-password`).
- Docker Compose deployment with pinned, non-root images, read-only root filesystems and dropped capabilities; GitHub Actions CI; browser E2E suite against the simulator.

# Changelog

## 1.0.0-rc.1 — release candidate (unreleased)

First release candidate: Phases 1–7 and 9 plus Phase 10 hardening. Validated against the built-in simulators only; no live UniFi console, host or model server has been used. Phase 8 (advanced AI) is tracked separately.

### Added
- `warden-admin reset-password <user> [--clear-mfa]`: reset a forgotten password from the server shell; signs out the account's sessions and is audited.

### Changed
- Gateways are recognised by model when the console omits the `gateway` feature (a live UDM Beast on 10.6.106 does), so gateway restarts stay high-risk and protected. The simulator's gateway now reports features the same way.
- The change engine refuses `DERIVED` firewall policies (UniFi's "(Return)" rules); only `USER_DEFINED` ones are changeable or flagged by analysis. The simulator includes one.
- SNMP: a target added by address before its controller was synced attaches to the live device at that address on its next poll (or, for a gateway listed under its WAN address, the device whose MAC one of its interfaces reports), so it gains port matching and the device-page panel without being re-added.

### Security
- Route-coverage test: every API route must be authenticated (except sign-in, setup, health and the MCP endpoint, which authenticates itself) and, outside core, gated by its module.
- CSRF header now also required on sign-in, MFA verify, setup and sign-out (closes login CSRF).
- Sign-in limits: per username from any address (50 / 15 min) and per address across usernames (20 / 15 min, only when client addresses are trustworthy) on top of the per-account limit; password confirmation and MFA activation are limited too; throttle memory is bounded.
- Client addresses: uvicorn no longer trusts `X-Forwarded-For` from anyone (`--forwarded-allow-ips=*` let a client choose its own address and step around sign-in limits). New `WARDEN_FORWARDED_HOPS` (default 0; 1 behind a TLS reverse proxy).
- The half-signed-in session token is replaced with a fresh one when MFA completes.
- SSRF guard for every user-supplied address (controllers, servers, Protect/Home Assistant, AI providers, webhooks, ntfy, SMTP, WAN probe, SNMP): http/https only, no credentials in URLs, link-local/metadata/unspecified/multicast/reserved refused unless allowed (`WARDEN_OUTBOUND_*`); HTTP connections re-checked after DNS resolution and on redirects.
- Request bodies over 2 MiB are refused with 413 (also when streamed without a length); list fields bounded.
- Secrets are redacted from audit details and log lines; authorization denials are audited (`authz.denied`).
- First-run owner creation serialised (two concurrent requests can no longer create two owners).
- Security headers: API adds a deny-all CSP, `Cache-Control: no-store` and CORP; the web app sends CSP, frame, referrer, COOP and permissions policies.

### Added
- Encrypted backup and restore of application state: owner-only `POST /api/backup/export` and `/api/backup/restore`, `warden-backup export|inspect|restore` CLI; AES-256-GCM under a scrypt passphrase key, optional vault key with credential re-keying on restore. See OPERATIONS.md.
- Browser E2E suite (Playwright, `pnpm e2e`): setup, sign-in, devices, firewall, change propose → approve → apply → verify on the simulator, logs, SOC, audit. Runs headless and offline.
- GitHub Actions CI: API tests/lint/format, web typecheck/lint/build, E2E. No secrets.

### Changed
- Version 1.0.0-rc.1 across the API, web app, bundled modules and images.
- Docker: base images pinned by version and digest, non-root users with read-only application files, compose services with read-only root filesystems, dropped capabilities, `no-new-privileges`, log rotation and a web health check.
- Docs: DEPLOYMENT (hardening, TLS/reverse proxy, configuration), OPERATIONS (health, backups, restore, upgrades, sizing), TESTING, SECURITY_MODEL, MODULE_ARCHITECTURE (catalog table regenerated).

### Upgrade notes
- Clients that call `POST /api/auth/login`, `/api/auth/logout`, `/api/auth/mfa/verify` or `/api/setup/owner` with a cookie session must send `X-Warden-CSRF: 1` (the web app always did). Bearer-token clients are unaffected.
- Behind a TLS reverse proxy, set `WARDEN_FORWARDED_HOPS=1` to keep real client addresses in the audit trail.
- Saved addresses that point at link-local or metadata destinations stop working until allowed with `WARDEN_OUTBOUND_ALLOW_LINK_LOCAL` or `WARDEN_OUTBOUND_ALLOWED_CIDRS`.
- No database migration in this release.

## 0.1.0 — development history
- Phase 0 research: UniFi Network/Protect official API matrices.
- Phase 8: advanced AI. Five specialised agents (Wi-Fi and RF review, Capacity and IP plan, Compliance and security review, Summarise this SOC case, Explain this change with the digital twin) and "Ask the network" over seven new read-only tools; autonomy "act after approval" (the approving person's approval also applies the agent's proposal through `engine.apply`, in their name; the agent still only proposes); background runs with live progress and a per-run stop; scheduled runs as their creator with a daily cap; agent notes with agent suggestions held for review. Change requests gain `apply_on_approval` and the approve endpoint an optional `apply`. Pages: Ask the network, Agent Center schedules and notes, live run page, "Approve and apply" on change detail. Migration `0013` (after `0011`). Tested only against the offline demo model and fake model servers.
- Phase 9: UniFi Protect (cameras and NVR read-only from the official Integration API, matched to network clients by MAC with uplink and documented port/PoE; findings; Cameras page), Home Assistant (read-only REST API; temperature, UPS, connectivity and router-tracker entities, trackers matched to clients; GPS trackers and location never stored; findings; page), MCP gateway (`/api/mcp`, Streamable HTTP, 7 read-only tools, API-token auth with `mcp:use` and per-tool scoping, audited; Account page guide). All three with in-process simulators and published-source matrices. New permissions `integrations:manage`, `mcp:use`. Migration `0012`.
- Phase 6d: Bulk Operations (one operation over up to 50 targets, proposed and approved once, applied item by item through the change engine with per-item re-read, drift check, verification and evidence; optional pause; stops at the first item that does not succeed and cancels the rest; no new write path), Automation Studio (event or schedule rules with conditions whose only actions are a change proposal or a notification; dry run over the last 7 days; run history; cooldown; audited), Network Digital Twin impact preview (devices, clients, networks, zones and policies a change or batch touches, from the observed inventory graph; snapshots not built). PoE cycles on different ports of one switch may be open at once. Pages: Bulk operations, Automation, "What it touches" on change and batch pages. Migration `0010`.
- Phase 7: optional AI runtime. AI Providers (Ollama, OpenAI-compatible, Anthropic Claude Messages API, offline demo model) with vault-stored keys, connection test, model listing, usage, cost and monthly budgets; AI Agent Center with three read-only agents (alert explainer, client slowness, firewall review) over a fixed permission-scoped tool set, autonomy up to "propose" (change proposals through the change engine only), global and per-agent kill switches, per-run limits, audited runs with full transcripts; AI-generated answers labelled in the UI. New permissions `ai:use` (operator and up) and `ai:manage` (admin, owner). Migration `0011`.
- Phase 6c: network rename, internet access, isolation and move-to-zone, and custom zone rename, through a generic checked read-modify-write (`FieldEdit`); protected when the network is the default or carries the controller's address; Networks page "Change…" dialog with choices.
- Phase 6b: enable/disable firewall policies by checked read-modify-write (full PUT minus server fields; every other field verified unchanged; original object restored on mismatch); system-defined policies are never changed; entry points hidden where the controller version lacks the operation; firewall list gets a Change column.
- Phase 6a: Change Approval & Recovery — the single write path: proposals with live state, diff, risk and protected paths; approval with optional second person; per-controller write mode (read-only by default, owner only); apply with conflict detection; verification by re-reading; automatic revert; evidence and audit. Operations: firewall policy logging, device restart, PoE power-cycle. Simulator accepts these writes and can inject faults. Migration `0009`.
- Phase 5d: SNMP Port Counters — read-only SNMP v3/v2c IF-MIB collector (pysnmp) with an OID-level simulator for the demo devices; credential profiles in the vault, targets from inventory, per-port throughput, utilisation, errors and discards; `port.*` metrics, built-in "Port errors" alert rule, findings; Port counters page and device-page counters. New permission `snmp:manage`. Migration `0008`.
- Phase 5b (backend): Log Management (UDP syslog receiver, RFC 3164/5424/CEF parsing, search, stats, retention), SOC (signals + cases), Investigation workbench. New permission `soc:manage`. Migration `0007`. Compose publishes syslog on localhost:5514/udp.
- Phase 5a (backend): Firewall & Policy Manager (exact shadowing/redundancy analysis, zone matrix), Configuration History (snapshots on change, semantic diffs, baseline/drift, `config.changed`), Policy & Compliance (11 controls, waivers, evidence CSV). New permission `compliance:manage`. Migration `0006`.
- Phase 4b: Servers & Virtualization — read-only Proxmox VE, Unraid (GraphQL API), Docker Engine and node_exporter collectors with simulators; hosts, VMs/LXCs and containers matched to network clients; storage, disk, UPS and autostart findings; host metrics. New permission `servers:manage`. Migration `0005`.
- Phase 4a: Rack & DCIM (locations, elevations, occupancy validation), Cabling & Fiber (tracing through patch panels, schedule, labels), Power & Environment (feeds, UPS/PDU chains, failover load, runtime estimate, PoE upper bound), Asset Lifecycle (renewal calendar, expiry and stock findings), Network Topology (uplink tree, logical view, VLAN overlay), Documentation & Wiki (versioned pages with live inventory references); labelled demo layout; search covers racks, cables, assets and pages. New permissions `dcim:edit`, `docs:edit`. Migration `0004`.
- Phase 3: metric history store with rollups, Analytics Studio dashboards, Monitoring & Alerts (rules, maintenance windows), Notification Channels (webhook, ntfy, email), WAN page with opt-in probe; simulator statistics follow a daily cycle. Migration `0003`.
- Phase 2: Client Intelligence (sightings, Client 360, client identity), IPAM (prefixes, reservations, VLAN records, planned-vs-observed findings, address map), device asset records, wider search. Migration `0002`.
- Phase 1 core: auth + MFA + tokens, RBAC, vault, audit chain, modules, scheduler, event bus, UniFi read-only integration with capability detection, simulator, inventory sync, web UI.

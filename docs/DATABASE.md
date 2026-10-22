# Database

PostgreSQL in deployment, SQLite for development and tests. Schema managed by Alembic (`apps/api/src/warden/migrations`), applied automatically at startup (`WARDEN_RUN_MIGRATIONS_ON_START`). A test asserts the migrated schema equals the ORM models.

## Platform tables
`users`, `role_assignments` (role per user), `auth_sessions` (hashed tokens, MFA-pending flag), `api_tokens` (hashed, permission list), `credentials` (Fernet ciphertext + fingerprint), `module_states`, `audit_records` (seq, hash chain), `events` (typed bus history), `job_runs`, `app_settings` (incl. per-user preferences).

## Inventory tables
`controllers` (connection, write_mode, capability report), `sites`, `devices`, `ports`, `radios`, `clients`, `networks` (incl. IPv4/DHCP config JSON), `wans`, `firewall_zones`, `firewall_policies`.

`clients` and `devices` also carry user-owned fields discovery never overwrites: `labels`, client `alias`/`owner`/`notes`, device `notes` and `asset` (JSON asset record).

## Infrastructure and servers tables
`dcim_locations`, `dcim_racks`, `dcim_rack_items` (optional unique link to a device), `dcim_cables`, `dcim_cable_ends` (two per cable), `dcim_power_feeds`, `dcim_power_links`, `assets`, `doc_pages`, `doc_versions`. All user-owned with `is_demo`. See [INFRASTRUCTURE.md](INFRASTRUCTURE.md).

Servers: `host_connections` (credential in `credentials`), and observed `hosts`, `workloads`, `host_storage` with the shared provenance columns. See [SERVERS.md](SERVERS.md).

## Analytics and alerting tables
`metric_series`, `metric_samples` (tiered metric history), `dashboards`, `alert_rules`, `alerts`, `maintenance_windows`, `notification_channels` (secrets in `credentials`), `notification_deliveries`. See [ANALYTICS.md](ANALYTICS.md).

## Client intelligence and IPAM tables
`client_sightings` (Client Intelligence: continuous stretches of a client at one IP/uplink/type), `ip_prefixes`, `ip_reservations`, `vlan_records` (IPAM documentation, user-owned). See [IPAM.md](IPAM.md).

Observed rows share: `external_id` (controller id), `source` (`unifi-integration-api@<version>` or `simulator@<version>`), `is_demo`, `first_seen_at`, `observed_at`, `present`, `raw` (last payload). Identity is `(site_id, external_id)`; platform `id` is stable across syncs.

## Retention
Events and audit records are kept indefinitely; job runs older than 30 days and expired sessions are pruned by `core.housekeeping`. Metric samples follow the tier retention in [ANALYTICS.md](ANALYTICS.md) (`core.metrics`); notification deliveries are kept 30 days and resolved alerts one year.

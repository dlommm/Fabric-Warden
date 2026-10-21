# Database

PostgreSQL in deployment, SQLite for development and tests. Schema managed by Alembic (`apps/api/src/warden/migrations`), applied automatically at startup (`WARDEN_RUN_MIGRATIONS_ON_START`). A test asserts the migrated schema equals the ORM models.

## Platform tables
`users`, `role_assignments` (role + optional site scope, not yet enforced), `auth_sessions` (hashed tokens, MFA-pending flag), `api_tokens` (hashed, permission list), `credentials` (Fernet ciphertext + fingerprint), `module_states`, `audit_records` (seq, hash chain), `events` (typed bus history), `job_runs`, `app_settings` (incl. per-user preferences).

## Inventory tables
`controllers` (connection, write_mode, capability report), `sites`, `devices`, `ports`, `radios`, `clients`, `networks` (incl. IPv4/DHCP config JSON), `wans`, `firewall_zones`, `firewall_policies`.

Observed rows share: `external_id` (controller id), `source` (`unifi-integration-api@<version>` or `simulator@<version>`), `is_demo`, `first_seen_at`, `observed_at`, `present`, `raw` (last payload). Identity is `(site_id, external_id)`; platform `id` is stable across syncs.

## Retention
Events and audit records are kept indefinitely for now; job runs older than 30 days and expired sessions are pruned by `core.housekeeping`. Partitioned metric storage arrives with Phase 3.

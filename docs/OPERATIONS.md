# Operations

Day-two tasks for a running installation: health, backups and restore, upgrades and migrations, sizing, logs. Deployment itself is in [DEPLOYMENT.md](DEPLOYMENT.md).

## Health checks

| Endpoint | Auth | Meaning |
|---|---|---|
| `GET /api/health/live` | none | The API process answers. |
| `GET /api/health/ready` | none | The API can query its database. The image's `HEALTHCHECK` fetches it through the web app's `/api` proxy. |
| `GET /api/system/health` | `system:read` | Database, Redis, scheduler and audit-chain status, failed job runs, version. Shown on the System page. |

A healthy container therefore means the web app and the API both answer. The API starts answering only after migrations have run, so readiness also means the schema is current.

## Forgotten password

From the server, reset any account's password; you are prompted for the new one, every session for that account is signed out, and the reset is audited (`auth.password.reset`, actor `warden-admin`). Add `--clear-mfa` if the authenticator is lost too.

```bash
docker compose -f deploy/docker-compose.yml exec app warden-admin reset-password admin
```

Anyone who can run this already controls the database, so it adds no new way in.

## Backups

A backup is an encrypted, portable dump of the application database. It is **owner-only** (`system:manage`).

**What is in it:** every table (inventory, history, metrics, logs, audit trail, change requests, documentation, settings, users, API token hashes, encrypted credentials) and, unless you leave it out, the vault master key. **Not in it:** sign-in sessions, the Redis cache (nothing durable lives there), container images.

**Encryption:** the archive is gzip-compressed JSON encrypted with AES-256-GCM under a key derived from your passphrase with scrypt (n=2^15, r=8, p=1); the header is authenticated. Passphrases must be at least 12 characters. Lose the passphrase and the backup is unrecoverable; there is no escrow.

**Vault key:** stored credentials (controller API keys, host tokens, TOTP secrets, notification secrets, AI provider keys) are encrypted with `WARDEN_SECRET_KEY` (or `data/vault.key`). By default the backup carries that key *inside the encryption*, so it restores on any installation: restore re-encrypts every credential under the restoring installation's own key. Choose "leave the vault key out" (API `include_vault_key: false`, CLI `--no-vault-key`) to keep the key out of the archive; such a backup only restores onto an installation that already uses the same key, and restore refuses otherwise rather than producing undecryptable credentials. Either way, keep a copy of `WARDEN_SECRET_KEY` with your secrets, separate from backups.

### Making a backup

API (session or an owner's API token, so it can be scheduled):

```bash
curl -fsS -X POST https://warden.example/api/backup/export \
  -H "Authorization: Bearer $WARDEN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"passphrase\": \"$BACKUP_PASSPHRASE\"}" -o "warden-$(date +%F).fwbk"
```

CLI inside the container (works while Fabric Warden is running; the export is one read transaction):

```bash
docker compose -f deploy/docker-compose.yml exec -e WARDEN_BACKUP_PASSPHRASE app \
  warden-backup export /data/backups/warden.fwbk
docker compose -f deploy/docker-compose.yml cp app:/data/backups/warden.fwbk ./
warden-backup inspect ./warden.fwbk   # decrypts and prints the summary (row counts, version, revision)
```

The passphrase comes from `WARDEN_BACKUP_PASSPHRASE` or a prompt, never an argument. Files are written `0600`. Every export is in the audit trail (`backup.export`).

To back up on a schedule, run the API call or CLI from cron or a systemd timer on the host.

### Restoring

Restore **replaces every row** in this installation's database with the backup's, in one transaction, then signs everyone out.

- The backup's schema revision must equal this installation's. Restore into the same Fabric Warden version the backup was made with, then upgrade (migrations run at start).
- Over the API (`POST /api/backup/restore`, owner session only, never a token): body `{archive_b64, passphrase, current_password, confirm: "RESTORE"}`. Scheduled jobs pause during the restore. Afterwards sign in with an account from the backup and **restart the `app` container** so in-memory state (module enablement, scheduler) is rebuilt. Size limit: `WARDEN_MAX_RESTORE_BYTES` (512 MiB).
- From the CLI (recommended for disaster recovery; stop Fabric Warden first):

  ```bash
  docker compose -f deploy/docker-compose.yml stop app
  docker compose -f deploy/docker-compose.yml run --rm -e WARDEN_BACKUP_PASSPHRASE \
    -v "$PWD/warden.fwbk:/restore.fwbk:ro" app warden-backup restore /restore.fwbk --yes
  docker compose -f deploy/docker-compose.yml up -d
  ```

  On an empty database the CLI applies migrations first.

The restored audit chain is verified like any other and continues with a `backup.restore` record. Failed or refused restores are audited too and change nothing.


### PostgreSQL-native backups

For large installations or point-in-time recovery, back up PostgreSQL directly instead of (or as well as) the portable archive:

```bash
docker compose -f deploy/docker-compose.yml exec -T db pg_dump -U warden -d warden -Fc > warden-$(date +%F).dump
# restore into an empty database of the same Fabric Warden version:
docker compose -f deploy/docker-compose.yml exec -T db pg_restore -U warden -d warden --clean --if-exists < warden.dump
```

A `pg_dump` contains credentials encrypted with the vault key but **not** the key: back up `WARDEN_SECRET_KEY` separately. Sessions are included; revoke them after restoring if the dump may have leaked (Users → sign out everywhere, or delete from `auth_sessions`).

## Upgrades and migrations

1. Make a backup (above) and note the current version (System page or `/api/system/health`).
2. Pull the new release (`git pull` / new tag) and read `docs/CHANGELOG.md` for breaking changes.
3. `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build`.
4. The API applies Alembic migrations at start (`WARDEN_RUN_MIGRATIONS_ON_START=true`), inside a transaction on PostgreSQL. Watch `docker compose logs -f app` until it reports `Fabric Warden … started` and the container is `healthy`.
5. Check the System page: database ok, audit chain intact, no new failed job runs.

Rollback: migrations are not written to be downgraded in production. To go back, stop the stack, restore the pre-upgrade backup into the previous version (same schema revision), then start the previous images.

Running one API replica is the supported setup. With more replicas, set `WARDEN_REDIS_URL` (job locks) and start one replica first so only it migrates.

## Sizing

One `docker stats` snapshot of the development stack (demo controller and simulators, a few days of history); real sites scale with device/client counts, metric retention and log volume.

| Component | Memory observed | Notes |
|---|---|---|
| API | ~130 MB | CPU spikes while polling jobs run (every 30–60 s); more with many controllers, hosts or SNMP targets |
| Web | ~60 MB | Static after build |
| PostgreSQL | ~180 MB incl. cache | Metric samples and log events dominate disk growth |
| Redis | ~10 MB | Locks only, no persistence |

Image: about 620 MB unpacked (API and web app together).

Plan for 1 vCPU and 1–2 GB RAM for a home lab (a few switches/APs, <200 clients), 2 vCPU / 4 GB for a small office or MSP pilot. Disk: a few GB; log management keeps `WARDEN_LOG_RETENTION_DAYS` (14) days capped at `WARDEN_LOG_MAX_ROWS` (2 M) rows, metrics follow the tiered retention in [ANALYTICS.md](ANALYTICS.md). These figures are estimates, not load-tested limits (see [TESTING.md](TESTING.md)).

## Logs

Container logs are JSON-file with rotation (10 MB × 5) in the compose file. Secrets are masked in application log lines (bearer tokens, `X-API-KEY`, `password=`, `token=`, Fabric Warden `wdn_` tokens, credentials in URLs) and in audit details (keys that name a secret). Uvicorn access logs record method, path and status, not bodies.

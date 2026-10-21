# Deployment

## Docker Compose

```bash
cp deploy/.env.example deploy/.env      # set POSTGRES_PASSWORD and WARDEN_SECRET_KEY (openssl rand -base64 32)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Open `http://<host>:8080` (or `WARDEN_HTTP_PORT`) and complete setup. Services: `db` (PostgreSQL 17), `redis` (job coordination, no persistence), `api` (not published), `web` (published).

**Status:** the compose file and Dockerfiles are written but have not yet been built end-to-end on the development host (its `/var` partition was full). Treat as pre-release.

### TLS
Put a reverse proxy (Caddy, Traefik, Nginx Proxy Manager, SWAG) in front of `web` and keep `WARDEN_COOKIE_SECURE=true`. Only set it to `false` for plain HTTP on a trusted LAN.

### Backups
Back up the `db` volume **and** `WARDEN_SECRET_KEY`. Without the key, stored controller API keys and TOTP secrets cannot be decrypted.

## Configuration (`WARDEN_*`)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite in `./data` | `postgresql+asyncpg://…` in production |
| `REDIS_URL` | unset | Enables cross-replica job locking |
| `SECRET_KEY` | generated `data/vault.key` | Vault master key |
| `COOKIE_SECURE` | `true` | Secure session cookie |
| `SESSION_TTL_HOURS` | `12` | Session lifetime |
| `DEMO_CONTROLLER_ENABLED` | `true` | Allow the labelled simulator |
| `SCHEDULER_ENABLED` | `true` | Background polling |

Unraid and Proxmox guides: see [UNRAID.md](UNRAID.md), [PROXMOX.md](PROXMOX.md) (to be written with the first release).

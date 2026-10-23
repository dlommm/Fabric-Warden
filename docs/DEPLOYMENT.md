# Deployment

Day-two tasks (backups, restore, upgrades, sizing) are in [OPERATIONS.md](OPERATIONS.md).

## Docker Compose

```bash
cp deploy/.env.example deploy/.env      # set POSTGRES_PASSWORD and WARDEN_SECRET_KEY (openssl rand -base64 32)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Open `http://<host>:8080` (or `WARDEN_HTTP_PORT`) and complete setup. Services: `db` (PostgreSQL 17), `redis` (job coordination, no persistence) and `app` (Fabric Warden: the web app on the published port, the API on the container's loopback behind it). `--build` builds the image from this checkout; without it, compose uses the published `dendlomm/fabric-warden` image of the same version.

## Single container

The published image runs on its own, with SQLite and the vault key in `/data`:

```bash
docker run -d --name fabric-warden --restart unless-stopped \
  -p 8080:3000 -v fabric-warden-data:/data \
  -e WARDEN_COOKIE_SECURE=false \
  dendlomm/fabric-warden:latest
```

Keep `WARDEN_COOKIE_SECURE=false` only for plain HTTP on a trusted LAN (see TLS below). Add `-e WARDEN_SYSLOG_PORT=5514 -p 5514:5514/udp` to receive syslog. Every `WARDEN_*` variable in the table below works the same way; point `WARDEN_DATABASE_URL` and `WARDEN_REDIS_URL` at PostgreSQL and Redis to use them instead of SQLite.

Images are published for `linux/amd64` and `linux/arm64`. Every release has its own tag (`1.1.0`), and `latest` is the newest release.

Docker keeps images and build cache under `/var/lib/docker` and `/var/lib/containerd`. If that partition is small, move both to a larger disk (for example bind-mount them from `/home`) before building.

## Image and container hardening

- **Pinned bases:** `python:3.13.17-slim-bookworm`, `node:24.22.1-bookworm-slim`, `ghcr.io/astral-sh/uv:0.12.19`, `postgres:17.11-alpine`, `redis:7.4.11-alpine`, each by version **and digest**. To update, bump the tag and replace the digest with the one `docker buildx imagetools inspect <image:tag>` prints, in the same change.
- **Multi-stage build:** build tools (uv, pnpm, corepack, the full `node_modules`) stay in build stages. The runtime contains the API's virtualenv (installed non-editable), the Next.js standalone server and the `node` binary, without npm or corepack.
- **Non-root:** both processes run as `warden` (uid 10001). Application files are owned by root and not writable by the service user. Only `/data` and `/app/web/.next/cache` are writable.
- **Compose:** a `read_only` root filesystem for `app` with `tmpfs` mounts for `/tmp` and the Next.js cache, `cap_drop: [ALL]`, `no-new-privileges` on every service, log rotation (10 MB × 5). The API listens on `127.0.0.1` inside the container and is never published; syslog is published on localhost only by default.
- **Health checks:** the image fetches `GET /api/health/ready` through the web app, so a healthy container means both processes answer and the database is reachable. Compose starts `app` only after `db` and `redis` are healthy.
- **One process tree:** the entrypoint starts the API and the web app; if either exits, it stops the other and the container exits, so Docker's restart policy brings both back. `docker stop` shuts both down cleanly.
- **Proxy headers:** uvicorn runs with `--no-proxy-headers`. Client addresses for audit and sign-in limits come from `WARDEN_FORWARDED_HOPS` (below), never from a header the browser can set.

## TLS and reverse proxy

Put a reverse proxy (Caddy, Traefik, Nginx Proxy Manager, SWAG) in front of `app`, terminate TLS there and keep `WARDEN_COOKIE_SECURE=true`. Only set it to `false` for plain HTTP on a trusted LAN.

When a reverse proxy is in front, set `WARDEN_FORWARDED_HOPS=1` so audit records and the per-address sign-in limit see real client addresses. The proxy must **set or append** `X-Forwarded-For` (Caddy and Traefik do by default; nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`). The web app's `/api` proxy passes the header through unchanged, so without a reverse proxy the header is whatever the browser sent: leave `WARDEN_FORWARDED_HOPS=0` then (every request is attributed to `127.0.0.1`, the web app inside the container, and the per-address limit is off; the per-account limits still apply).

Caddy example:

```
warden.example.lan {
    reverse_proxy 127.0.0.1:8080
    header Strict-Transport-Security "max-age=31536000; includeSubDomains"
}
```

The web app sends `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy` and `Permissions-Policy` itself. HSTS belongs at the TLS proxy (or build the image with `--build-arg WARDEN_HSTS=true` when the site is only ever served over HTTPS). The CSP allows inline scripts and styles because Next.js inlines its bootstrap; everything else is limited to the same origin.

## Backups

Use the encrypted backup (`warden-backup export` or `POST /api/backup/export`, owner-only) and keep `WARDEN_SECRET_KEY` with your secrets. Without the key, credentials in a plain database dump cannot be decrypted. Full procedure, restore and PostgreSQL-native dumps: [OPERATIONS.md](OPERATIONS.md#backups).

## Upgrades

Back up, pull the new release, `up -d --build`; migrations run at API start. Details and rollback: [OPERATIONS.md](OPERATIONS.md#upgrades-and-migrations).

## Configuration (`WARDEN_*`)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite in `./data` (`/data/warden.db` in the image) | `postgresql+asyncpg://…` in production |
| `REDIS_URL` | unset | Enables cross-replica job locking |
| `SECRET_KEY` | generated `data/vault.key` | Vault master key |
| `COOKIE_SECURE` | `true` | Secure session cookie |
| `SESSION_TTL_HOURS` | `12` | Session lifetime |
| `DEMO_CONTROLLER_ENABLED` | `true` | Allow the labelled simulator |
| `SCHEDULER_ENABLED` | `true` | Background polling |
| `PUBLIC_URL` | unset | Base URL used in notification links |
| `FORWARDED_HOPS` | `0` | Trusted proxies that set `X-Forwarded-For` in front of Fabric Warden (see TLS above) |
| `MAX_REQUEST_BYTES` | `2097152` | Largest accepted request body (413 above it) |
| `MAX_RESTORE_BYTES` | `536870912` | Largest accepted backup restore upload |
| `LOGIN_IP_MAX_FAILURES` | `20` | Failed sign-ins per address per 15 min (only when `FORWARDED_HOPS` > 0) |
| `LOGIN_USER_MAX_FAILURES` | `50` | Failed sign-ins per username from any address per 15 min |
| `OUTBOUND_ALLOW_LINK_LOCAL` | `false` | Allow connections to link-local addresses (169.254.0.0/16, fe80::/10) |
| `OUTBOUND_ALLOWED_CIDRS` | `[]` | JSON list of networks exempt from every outbound block, e.g. `["169.254.10.0/24"]` |
| `OUTBOUND_BLOCKED_CIDRS` | `[]` | JSON list of extra networks to refuse, e.g. the Docker bridge |
| `SYSLOG_PORT` / `SYSLOG_BIND` | `0` / `0.0.0.0` | Syslog receiver (compose uses 5514) |
| `LOG_RETENTION_DAYS` / `LOG_MAX_ROWS` | `14` / `2000000` | Log Management retention |

Outbound policy: Fabric Warden connects to equipment on your network, so private and loopback addresses are allowed. Link-local and cloud-metadata destinations (169.254.169.254, `metadata.google.internal`, …), unspecified, multicast and reserved addresses are refused for controllers, servers, Protect/Home Assistant, AI providers, notification webhooks/ntfy/SMTP, WAN probe targets and SNMP targets when they are saved. HTTP(S) connections are checked again at connect time after DNS resolution, so a name that resolves to a blocked address (or a redirect to one) is refused too; SMTP hosts are re-checked by literal address only, and WAN probe and SNMP hosts only when saved.


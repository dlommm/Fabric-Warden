# Architecture

## Shape

A **modular monolith**: one FastAPI process (`apps/api`, package `warden`) owns the domain, plus one Next.js app (`apps/web`) that serves the UI and proxies `/api/*` to the backend so the browser only ever talks to one origin. Both ship in one container image: the web app is the published port and the API listens on the container's loopback behind it.

```
browser ──► web (Next.js, :3000) ──/api/*──► api (FastAPI, :8000) ──► PostgreSQL
                                                  │                 └─► Redis (job locks)
                                                  └─► UniFi console (official Integration API, read-only)
```

## Backend layout (`apps/api/src/warden`)

| Path | Responsibility |
|---|---|
| `config.py` | Settings from `WARDEN_*` env vars |
| `db.py` | Async SQLAlchemy engine, `UTCDateTime`, naming conventions |
| `models/` | `platform.py` (users, sessions, tokens, credentials, module state, audit, events, job runs, settings), `inventory.py` (controllers, sites, devices, ports, radios, clients, networks, WANs, zones, policies) |
| `security/` | `crypto.py` (argon2, tokens), `vault.py` (Fernet credential vault), `rbac.py` (permissions and roles), `outbound.py` (SSRF policy + guarded transport), `limits.py` (body size, throttles, security headers), `redact.py` (secret masking for audit and logs) |
| `backup.py`, `backup_cli.py` | Encrypted portable backup and restore (`/api/backup`, `warden-backup`) |
| `audit.py` | Hash-chained audit records + `verify_chain` |
| `events.py` | Typed event bus: persisted + in-process subscribers + SSE listeners |
| `jobs.py` | Scheduler; jobs run only while their module is enabled; Redis lock when configured |
| `modules/` | Contract (`ModuleManifest`), bundled catalog + presets, lifecycle service |
| `integrations/unifi/` | Endpoint matrix, capability evaluation, read-only client with write gate, simulator, sync |
| `api/` | Routers: auth, users/setup, modules, controllers, inventory, system (health, jobs, audit, events) |
| `migrations/` | Alembic; run automatically at startup |

## Key decisions (ADR summary)

1. **Modular monolith over microservices** — one deployable, one transaction boundary, modules isolated by manifest + permission + enablement rather than network hops.
2. **Same-origin proxy** — the API is not published; session cookies stay first-party, no CORS.
3. **Read-only by construction** — `UniFiNetworkClient` raises `WriteNotAuthorized` for any non-GET without a `WriteAuthorization`, which can only be minted with a sentinel the change engine will own. A test fails if any other module imports it.
4. **Capabilities from published specs, refined by probing** — never from UI feature presence.
5. **Provenance on every observed row** — `source`, `observed_at`, `first_seen_at`, `present`, `is_demo`, `raw`.
6. **History is retained** — objects that disappear are marked `present=false` and emit events; user-owned metadata (labels, notes) is never overwritten by discovery.
7. **Simulator in-process** — the demo controller is served through `httpx.ASGITransport`, so demos and tests need no network and no hardware.
8. **SQLite for dev/tests, PostgreSQL for deployment** — portable column types; Alembic `render_as_batch` for SQLite.

## Request authentication

Session cookie (`httpOnly`, `SameSite=Lax`, `Secure` in production) or `Authorization: Bearer wdn_…`. Cookie-authenticated unsafe methods, and sign-in, MFA verify, setup and sign-out, must send `X-Warden-CSRF: 1`. Client addresses come from the socket peer or the last `WARDEN_FORWARDED_HOPS` entries of `X-Forwarded-For`. See [SECURITY_MODEL.md](SECURITY_MODEL.md).

## Live updates

`GET /api/events/stream` (SSE) pushes bus events; the UI invalidates affected queries with a short debounce.

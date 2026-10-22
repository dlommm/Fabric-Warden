# Testing

Nothing in the test suites contacts a real controller, device, host, AI endpoint or other external service: every test uses SQLite and the in-process simulators (UniFi Network, Protect, Home Assistant, Proxmox/Unraid/Docker/node_exporter, SNMP, an offline model server), and outbound HTTP in tests goes through fake transports.

## Backend (`apps/api`)

```bash
cd apps/api && uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
```

All tests use a temporary SQLite database migrated with Alembic. Highlights:

- `test_auth.py`, `test_rbac.py` — setup, sign-in/out, MFA, tokens, preferences, role × endpoint matrix, last-owner protection
- `test_security.py` (Phase 10) — **every registered route is authenticated and module-gated** (fails when a new route forgets it); CSRF on sign-in/setup/sign-out; sign-in limits per address, per username, per account under spoofed `X-Forwarded-For`; password-confirmation limits; MFA session-token rotation; 413 for oversized and chunked bodies; bounded lists; SSRF refusal for link-local/metadata/unspecified/credential/non-HTTP URLs on every URL-accepting endpoint, policy overrides, connect-time DNS check in the guarded transport; secret redaction in audit details and logs; audited authorization denials; API security headers
- `test_backup.py` (Phase 10) — archive encrypted and authenticated (wrong passphrase, tampering), restore onto a second installation with a different vault key (credentials re-keyed, controller still syncs, sessions signed out, audit chain intact), restore without the vault key, schema-revision mismatch, owner-only/session-only guards, CLI round trip
- `test_modules.py`, `test_unifi.py` (including the **write gate** import test), `test_changes.py`, `test_phase*.py`, `test_ai.py`, `test_servers.py`, `test_snmp.py` — feature suites against the simulators
- `test_migrations.py` — schema drift; `test_audit.py` — tamper detection

## Frontend (`apps/web`)

```bash
cd apps/web && npx tsc --noEmit && pnpm lint && pnpm build
```

## Browser end-to-end (`apps/web/e2e`)

```bash
cd apps/web && pnpm exec playwright install chromium   # once
pnpm e2e                                               # or: pnpm e2e:dev (next dev, no build)
```

Playwright starts its own API on port 18000 (fresh SQLite database in `apps/web/.e2e-data`, scheduler on, demo controller allowed) and its own production web build on port 13000 (`.next-e2e`, so it never touches a dev build), then walks one installation in order:

1. first-run setup: owner account → demo controller → *Full Installation* preset and first sync
2. sign out (cookies cleared), redirect to sign-in, sign in; web security headers present
3. Devices: rows from the simulator, labelled *Demo data*
4. Firewall: policies in evaluation order
5. Changes: owner allows changes on the demo controller; a firewall-policy logging change is proposed from the Firewall page, approved, applied to the simulator and shown as *Applied and verified*
6. Logs: demo log lines loaded and labelled
7. SOC: opens with demo-labelled signals
8. Audit: the chain verifies and contains setup and the change's propose/approve/apply records; system health checks are ok

Runs headless and offline: the only controller is the simulator, and Google Fonts are replaced at build time by `e2e/fonts-offline.cjs` (via Next's `NEXT_FONT_GOOGLE_MOCKED_RESPONSES`), so the E2E build renders fallback fonts. Override ports with `E2E_API_PORT` / `E2E_WEB_PORT`. Failure traces and screenshots land in `test-results/`.

## CI

`.github/workflows/ci.yml` runs three jobs on pushes to `main` and on pull requests, with no secrets: API (ruff check, ruff format check, pytest), web (tsc, eslint, next build), and E2E (after both, installs Chromium and runs `pnpm e2e`; uploads the Playwright report on failure). Written in Phase 10; it has not run on GitHub yet.

## Not yet covered

PostgreSQL in the automated suites (backup restore was only exercised manually on PostgreSQL), Docker images in CI, real-controller compatibility, load/performance, live model red-teaming, multi-replica rate limiting.

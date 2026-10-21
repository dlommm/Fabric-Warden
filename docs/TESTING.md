# Testing

## Backend (`apps/api`, `uv run pytest`)
61 tests, all using a temporary SQLite DB migrated with Alembic and the in-process simulator — never a real controller.

- `test_auth.py` — setup once-only, weak passwords, login/logout, throttling, CSRF, TOTP enrolment and login, token narrowing/revocation, preferences, change password revokes other sessions
- `test_rbac.py` — role × endpoint matrix, admin can't grant owner, last owner protected, deactivation revokes sessions
- `test_modules.py` — catalog integrity, dependency closure, planned modules locked, cascade disable, disabled modules block API and jobs but keep data, presets
- `test_unifi.py` — capability matrix by version, pagination, auth errors, version-gated endpoints, **write gate**, sync provenance, state-change/missing events, idempotent re-sync, older-controller degradation, unreachable controller keeps inventory, keys encrypted at rest, user metadata survives resync, search, uplink resolution
- `test_audit.py` — tamper detection, denied logins recorded, system health
- `test_migrations.py` — schema drift

## Frontend (`apps/web`)
`npx tsc --noEmit`, `pnpm lint`, `pnpm build`. A Playwright walkthrough (setup → every screen, dark/compact, mobile) was used for visual review; turning it into a CI suite is tracked in the roadmap.

## Not yet covered
PostgreSQL run, Docker images, real-controller compatibility, load/performance, prompt-injection and change-engine tests (with their features).

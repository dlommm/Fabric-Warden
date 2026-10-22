# Contributing

1. Read `docs/ARCHITECTURE.md` and follow the hard rules below.
2. Backend changes need tests (`apps/api/tests`) and must pass `uv run pytest` and `ruff`.
3. Frontend changes must pass `tsc`, `eslint` and `next build`, and reuse the tokens in `apps/web/src/app/globals.css` and the components in `apps/web/src/components/ui/`. User-facing flows should keep the browser E2E suite green (`cd apps/web && pnpm e2e`, see `docs/TESTING.md`); CI (`.github/workflows/ci.yml`) runs all of these.
4. Schema changes need an Alembic migration; `test_migrations.py` catches drift.
5. Update `docs/CHANGELOG.md` and any affected doc in the same change. Don't describe unbuilt features as working.

## Hard rules

- Never add controller writes outside the change engine. Do not import `warden.integrations.unifi._write_gate` anywhere else (a test enforces this).
- Never invent UniFi endpoints or capabilities: update `official_api_matrix.json` from published specs and cite them in `docs/API_CAPABILITIES.md`.
- Never run tests or tools against a real controller. Use the simulator.
- Planned features stay `maturity=PLANNED` and docs say so.
- Demo data must stay labelled (`is_demo`).

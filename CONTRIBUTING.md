# Contributing

1. Read `CLAUDE.md` (applies to humans too) and `docs/ARCHITECTURE.md`.
2. Backend changes need tests (`apps/api/tests`) and must pass `uv run pytest` and `ruff`.
3. Frontend changes must pass `tsc`, `eslint` and `next build`, and follow `DESIGN.md`. User-facing flows should keep the browser E2E suite green (`cd apps/web && pnpm e2e`, see `docs/TESTING.md`); CI (`.github/workflows/ci.yml`) runs all of these.
4. Schema changes need an Alembic migration; `test_migrations.py` catches drift.
5. Update `docs/ROADMAP.md` and any affected doc in the same change. Don't describe unbuilt features as working.

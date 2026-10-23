# Contributing

1. Read `docs/ARCHITECTURE.md` and follow the hard rules below.
2. Backend changes need tests (`apps/api/tests`) and must pass `uv run pytest` and `ruff`.
3. Frontend changes must pass `tsc`, `eslint` and `next build`, and reuse the tokens in `apps/web/src/app/globals.css` and the components in `apps/web/src/components/ui/`. User-facing flows should keep the browser E2E suite green (`cd apps/web && pnpm e2e`, see `docs/TESTING.md`); CI (`.github/workflows/ci.yml`) runs all of these.
4. Schema changes need an Alembic migration; `test_migrations.py` catches drift.
5. Update any affected doc in the same change. Don't describe unbuilt features as working. `docs/CHANGELOG.md` is written from commit messages when a release is made.
6. Write commit messages (and pull request titles, for squash merges) as [Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`, `ci: …`, `chore: …`, `refactor: …`, `test: …`. The subject is what the changelog shows, so describe the change for someone running Fabric Warden.

## Releases

Versions are `vX.Y.Z` and come from the commits since the last release:

| Commits since the last release include | Next version |
|---|---|
| `feat!:` / `fix!:` or a `BREAKING CHANGE:` footer | major (2.0.0) |
| `feat:` | minor (1.1.0) |
| `fix:`, `perf:`, `security:` | patch (1.0.1) |
| only `docs:`, `ci:`, `chore:`, `refactor:`, `test:` | no release |

After tests pass on `main`, CI keeps a release pull request open with the next version in every version file and the changelog entry. Merging it tags `vX.Y.Z`, creates the GitHub release and publishes the Docker image as `dendlomm/fabric-warden:X.Y.Z` and `:latest`.

## Hard rules

- Never add controller writes outside the change engine. Do not import `warden.integrations.unifi._write_gate` anywhere else (a test enforces this).
- Never invent UniFi endpoints or capabilities: update `official_api_matrix.json` from published specs and cite them in `docs/API_CAPABILITIES.md`.
- Never run tests or tools against a real controller. Use the simulator.
- Unbuilt features stay `maturity=PLANNED` in the module catalog. Public docs describe only what is available; plans are not documented here.
- Demo data must stay labelled (`is_demo`).

# Fabric Warden web

The Next.js 15 frontend for Fabric Warden: client components with TanStack Query, styled with Tailwind and the tokens in `src/app/globals.css`. In development it proxies `/api` to the API on port 8000.

```bash
pnpm install
pnpm dev          # http://localhost:3000 (start the API first, see the root README)
pnpm lint && npx tsc --noEmit && pnpm build
pnpm e2e          # Playwright E2E against the simulator; starts its own API and web servers
```

See the [root README](../../README.md) and [docs/TESTING.md](../../docs/TESTING.md).

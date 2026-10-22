import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// Browser end-to-end suite. Starts its own API (fresh SQLite database in
// .e2e-data, in-process UniFi simulator, no real controller) and its own web
// build, both on ports that don't clash with a dev stack. Nothing reaches the
// network: the only controller is the built-in simulator and fonts are mocked
// at build time (e2e/fonts-offline.cjs).
const API_PORT = Number(process.env.E2E_API_PORT ?? 18000);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 13000);
const here = __dirname;
const data = path.join(here, ".e2e-data");
const api = path.join(here, "..", "api");

const apiCommand = [
  `rm -rf "${data}" && mkdir -p "${data}"`,
  `cd "${api}"`,
  "uv sync --frozen --quiet",
  // Run the venv binary directly (not `uv run`) so stopping the server stops Python too.
  `exec .venv/bin/uvicorn warden.main:create_app --factory --host 127.0.0.1 --port ${API_PORT} --no-access-log --timeout-graceful-shutdown 3`,
].join(" && ");

// E2E_WEB=dev uses `next dev` (no build; slower first page loads).
const webCommand =
  process.env.E2E_WEB === "dev"
    ? `exec ./node_modules/.bin/next dev -p ${WEB_PORT} -H 127.0.0.1`
    : `./node_modules/.bin/next build && exec ./node_modules/.bin/next start -p ${WEB_PORT} -H 127.0.0.1`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // one shared installation; the flow builds on itself
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: apiCommand,
      url: `http://127.0.0.1:${API_PORT}/api/health/ready`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      env: {
        WARDEN_DATABASE_URL: `sqlite+aiosqlite:///${data}/warden.db`,
        WARDEN_DATA_DIR: data,
        WARDEN_COOKIE_SECURE: "false",
        WARDEN_DEMO_CONTROLLER_ENABLED: "true",
        WARDEN_SCHEDULER_ENABLED: "true",
        WARDEN_SCHEDULER_TICK_SECONDS: "2",
        WARDEN_SYSLOG_PORT: "0",
        WARDEN_ENVIRONMENT: "e2e",
      },
    },
    {
      command: webCommand,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        WARDEN_API_URL: `http://127.0.0.1:${API_PORT}`,
        NEXT_DIST_DIR: ".next-e2e",
        NEXT_TELEMETRY_DISABLED: "1",
        NEXT_FONT_GOOGLE_MOCKED_RESPONSES: path.join(here, "e2e", "fonts-offline.cjs"),
      },
    },
  ],
});

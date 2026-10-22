import { expect, test, type Page } from "@playwright/test";

// One installation, walked in order: first-run setup, sign-in, the demo
// controller's inventory, firewall, a change proposed -> approved -> applied
// on the simulator and verified, logs, SOC and the audit trail.
test.describe.configure({ mode: "serial" });

const OWNER = { username: "owner", password: "correct horse battery staple 9" };

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(OWNER.username);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("first-run setup creates the owner, connects the demo controller and applies a preset", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup/);
  await expect(page.getByRole("heading", { name: "Create the owner account" })).toBeVisible();
  await page.getByLabel("Username").fill(OWNER.username);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByLabel("Confirm password").fill(OWNER.password);
  await page.getByRole("button", { name: "Create owner and continue" }).click();

  await page.getByRole("button", { name: "Use the demo controller" }).click();
  await expect(page.getByRole("heading", { name: "Choose what to switch on" })).toBeVisible();
  await page.getByRole("radio", { name: /Full Installation/ }).check();
  await page.getByRole("button", { name: "Apply and run first sync" }).click();
  await expect(page).toHaveURL(/\/$/, { timeout: 60_000 });
});

test("signs out and back in", async ({ page, context }) => {
  await signIn(page);
  await context.clearCookies();
  await page.goto("/inventory/devices");
  await expect(page).toHaveURL(/\/login/);
  await signIn(page);
  // Security headers from the web app reach the browser.
  const res = await page.request.get("/login");
  expect(res.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(res.headers()["x-frame-options"]).toBe("DENY");
});

test.describe("signed in", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("devices from the demo controller are listed and labelled as demo data", async ({ page }) => {
    await page.goto("/inventory/devices");
    await expect(page.getByRole("heading", { name: "Devices", level: 1 })).toBeVisible();
    const table = page.getByRole("table").first();
    await expect(table.getByRole("row").nth(3)).toBeVisible();
    await expect(page.getByText("Demo data").first()).toBeVisible();
  });

  test("firewall policies are shown", async ({ page }) => {
    await page.goto("/firewall");
    await expect(page.getByRole("heading", { name: "Firewall", level: 1 })).toBeVisible();
    await expect(page.getByRole("table", { name: "Policies in evaluation order" })).toBeVisible();
  });

  test("a firewall logging change is proposed, approved, applied on the simulator and verified", async ({ page }) => {
    // Every controller starts read-only; the owner allows changes first.
    await page.goto("/changes");
    await expect(page.getByRole("heading", { name: "Changes", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Allow changes" }).first().click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Allow changes" }).click();
    await expect(page.getByRole("button", { name: "Make read-only" }).first()).toBeVisible();

    await page.goto("/firewall");
    const propose = page.getByRole("button", { name: /^Turn on logging… for / }).first();
    await expect(propose).toBeVisible();
    await propose.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Why").fill("E2E: log matches for this policy");
    await dialog.getByRole("button", { name: "Propose change" }).click();
    await expect(page).toHaveURL(/\/changes\/[0-9a-f-]{36}$/);

    await page.getByRole("button", { name: "Approve" }).click();
    await page.getByRole("button", { name: "Apply now" }).click();
    await expect(page.getByText("Applied and verified").first()).toBeVisible({ timeout: 45_000 });
  });

  test("demo logs load and are labelled", async ({ page }) => {
    await page.goto("/logs");
    await expect(page.getByRole("heading", { name: "Logs", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Load demo logs" }).click();
    await expect(page.getByText("Includes demo log lines")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Last 24 hours · [1-9]\d* events/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove demo logs" })).toBeVisible();
  });

  test("the SOC opens with signals from the demo data", async ({ page }) => {
    await page.goto("/soc");
    await expect(page.getByRole("heading", { name: "SOC", level: 1 })).toBeVisible();
    await expect(page.getByText("Demo data").first()).toBeVisible();
  });

  test("the audit trail records the change and its chain verifies", async ({ page }) => {
    const res = await page.request.get("/api/audit?limit=200");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.chain_intact).toBe(true);
    const actions: string[] = body.records.map((r: { action: string }) => r.action);
    expect(actions).toEqual(expect.arrayContaining(["setup.owner_created", "change.propose", "change.approve", "change.apply"]));
    const health = await (await page.request.get("/api/system/health")).json();
    const checks = Object.fromEntries(health.checks.map((c: { id: string; status: string }) => [c.id, c.status]));
    expect(checks).toMatchObject({ database: "ok", scheduler: "ok", audit_chain: "ok" });
    await page.goto("/audit");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});

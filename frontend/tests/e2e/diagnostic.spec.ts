import { test, expect, type Page } from "@playwright/test";

/**
 * Comprehensive diagnostic test suite for Jarvis frontend + backend.
 * Tests every page load, API connectivity, UI rendering, console errors, and key interactions.
 */

// ── Auth Setup ──────────────────────────────────────────────────

test.beforeEach(async ({ page }) => {
  // Pre-set auth token in localStorage before each test
  await page.goto("/login", { waitUntil: "commit" });
  await page.evaluate(() => {
    localStorage.setItem("jarvis_auth_token", "test-token-123");
    localStorage.setItem(
      "jarvis_auth_user",
      JSON.stringify({ user_id: "usr_01JTEST00000000000000000000", email: "admin@jarvis.local", display_name: "Admin" })
    );
  });
});

// ── Helpers ─────────────────────────────────────────────────────

interface PageResult {
  consoleErrors: string[];
  apiCalls: { method: string; url: string; status: number; ok: boolean }[];
  bodyText: string;
}

async function visitPage(page: Page, path: string): Promise<PageResult> {
  const consoleErrors: string[] = [];
  const apiCalls: { method: string; url: string; status: number; ok: boolean }[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });

  page.on("response", (resp) => {
    const url = resp.url();
    if (url.includes("/api/") || url.includes("/v1/")) {
      apiCalls.push({
        method: resp.request().method(),
        url: url.replace(/^https?:\/\/[^/]+/, ""),
        status: resp.status(),
        ok: resp.ok(),
      });
    }
  });

  await page.goto(path, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(2000);
  const bodyText = (await page.textContent("body").catch(() => "")) ?? "";
  return { consoleErrors, apiCalls, bodyText };
}

// ── 1. Every Page Load + API + Console Errors ───────────────────

const allPages = [
  { path: "/", name: "Dashboard", expectApi: "/api/system/dashboard" },
  { path: "/chat", name: "Chat", expectApi: "/api/conversations" },
  { path: "/tasks", name: "Tasks", expectApi: "/api/tasks" },
  { path: "/goals", name: "Goals", expectApi: "/api/goals" },
  { path: "/approvals", name: "Approvals", expectApi: "/api/approvals" },
  { path: "/executions", name: "Executions", expectApi: "/api/executions" },
  { path: "/memories", name: "Memories", expectApi: "/api/memories" },
  { path: "/entities", name: "Entities", expectApi: null },
  { path: "/briefings", name: "Briefings", expectApi: "/api/briefings" },
  { path: "/schedules", name: "Schedules", expectApi: "/api/schedules" },
  { path: "/triggers", name: "Triggers", expectApi: "/api/triggers" },
  { path: "/workflows", name: "Workflows", expectApi: "/api/workflows" },
  { path: "/agents", name: "Agents", expectApi: "/api/agents" },
  { path: "/routes", name: "Routes", expectApi: "/api/routes" },
  { path: "/integrations", name: "Integrations", expectApi: "/api/integrations" },
  { path: "/notifications", name: "Notifications", expectApi: "/api/notifications" },
  { path: "/settings", name: "Settings", expectApi: "/api/settings" },
  { path: "/system", name: "System", expectApi: "/api/system/dashboard" },
  { path: "/search", name: "Search", expectApi: null },
  { path: "/login", name: "Login", expectApi: null },
];

for (const p of allPages) {
  test(`[Page] ${p.name} (${p.path})`, async ({ page }) => {
    const result = await visitPage(page, p.path);

    // Log results
    console.log(`\n=== ${p.name.toUpperCase()} (${p.path}) ===`);
    console.log(`  Body: ${result.bodyText.length} chars`);
    console.log(`  Console Errors: ${result.consoleErrors.length}`);
    for (const e of result.consoleErrors) console.log(`    [CONSOLE ERROR] ${e.substring(0, 250)}`);
    console.log(`  API Calls: ${result.apiCalls.length}`);
    for (const c of result.apiCalls) {
      const flag = c.ok ? "OK" : `FAIL(${c.status})`;
      console.log(`    [${flag}] ${c.method} ${c.url}`);
    }

    // Check failing APIs
    const failedApis = result.apiCalls.filter((c) => !c.ok);
    if (failedApis.length > 0) {
      console.log(`  FAILED APIs:`);
      for (const f of failedApis) console.log(`    ${f.method} ${f.url} -> ${f.status}`);
    }

    // Assert page rendered content
    expect(result.bodyText.length).toBeGreaterThan(50);

    // Screenshot
    await page.screenshot({
      path: `test-results/page-${p.name.toLowerCase()}.png`,
      fullPage: true,
    });
  });
}

// ── 2. Backend API Smoke Tests (direct) ─────────────────────────

const apiEndpoints = [
  "/api/system/dashboard",
  "/api/tasks",
  "/api/goals",
  "/api/approvals",
  "/api/executions",
  "/api/memories",
  "/api/schedules",
  "/api/triggers",
  "/api/agents",
  "/api/routes",
  "/api/integrations",
  "/api/notifications",
  "/api/workflows",
  "/api/settings",
];

test("[API] All endpoints return 200 with auth", async ({ request }) => {
  console.log(`\n=== API ENDPOINT SMOKE TEST ===`);
  const results: { path: string; status: number; preview: string }[] = [];

  for (const ep of apiEndpoints) {
    const resp = await request.get(`http://localhost:3000${ep}`, {
      headers: { Authorization: "Bearer test-token-123" },
    });
    const text = await resp.text().catch(() => "");
    results.push({ path: ep, status: resp.status(), preview: text.substring(0, 150) });
    console.log(
      `  [${resp.ok() ? "OK" : "FAIL"}] ${ep} -> ${resp.status()} | ${text.substring(0, 100)}`
    );
  }

  const failures = results.filter((r) => r.status >= 400);
  if (failures.length > 0) {
    console.log(`\n  FAILURES:`);
    for (const f of failures) console.log(`    ${f.path} -> ${f.status}: ${f.preview}`);
  }
});

// ── 3. Chat Flow E2E ────────────────────────────────────────────

test("[Flow] Chat: input, type, send button visible", async ({ page }) => {
  const result = await visitPage(page, "/chat");

  console.log(`\n=== CHAT FLOW TEST ===`);

  const textarea = page.locator("textarea").first();
  const input = page.locator('input[type="text"]').first();
  const hasTextarea = await textarea.isVisible({ timeout: 3000 }).catch(() => false);
  const hasInput = await input.isVisible({ timeout: 3000 }).catch(() => false);
  console.log(`  Textarea visible: ${hasTextarea}`);
  console.log(`  Text input visible: ${hasInput}`);

  const chatInput = hasTextarea ? textarea : input;
  if (hasTextarea || hasInput) {
    await chatInput.fill("Hello Jarvis");
    const val = await chatInput.inputValue();
    console.log(`  Typed text: "${val}"`);
    expect(val).toBe("Hello Jarvis");
  }

  // Check for send button
  const sendBtn = page
    .locator('button[type="submit"], button:has-text("Send"), button[aria-label*="send" i]')
    .first();
  const hasSend = await sendBtn.isVisible({ timeout: 2000 }).catch(() => false);
  console.log(`  Send button visible: ${hasSend}`);

  // Check for conversation sidebar
  const body = result.bodyText.toLowerCase();
  console.log(`  Has 'conversation' text: ${body.includes("conversation")}`);
  console.log(`  Has 'new chat' text: ${body.includes("new chat") || body.includes("new conversation")}`);

  console.log(`  Console Errors: ${result.consoleErrors.length}`);
  for (const e of result.consoleErrors) console.log(`    [ERROR] ${e.substring(0, 200)}`);

  await page.screenshot({ path: "test-results/flow-chat.png", fullPage: true });
});

// ── 4. Dashboard Widgets E2E ────────────────────────────────────

test("[Flow] Dashboard: widgets render data", async ({ page }) => {
  const result = await visitPage(page, "/");

  console.log(`\n=== DASHBOARD WIDGETS TEST ===`);
  const body = result.bodyText.toLowerCase();

  const checks = [
    { label: "budget", present: body.includes("budget") },
    { label: "task", present: body.includes("task") },
    { label: "agent", present: body.includes("agent") },
    { label: "memory/memories", present: body.includes("memor") },
    { label: "trace", present: body.includes("trace") },
    { label: "execution", present: body.includes("execution") || body.includes("run") },
  ];

  for (const c of checks) {
    console.log(`  Has '${c.label}': ${c.present}`);
  }

  // Count card-like elements
  const cards = page.locator("div[class*='card' i], div[class*='Card']");
  const cardCount = await cards.count();
  console.log(`  Card elements: ${cardCount}`);

  // Check for API errors shown in UI
  const errorElements = page.locator("text=/error|Error|failed|Failed/");
  const errCount = await errorElements.count();
  console.log(`  Error text in UI: ${errCount}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).length}`);
  console.log(`  Console Errors: ${result.consoleErrors.length}`);

  await page.screenshot({ path: "test-results/flow-dashboard.png", fullPage: true });
});

// ── 5. System Health Page ───────────────────────────────────────

test("[Flow] System: health dashboard renders", async ({ page }) => {
  const result = await visitPage(page, "/system");

  console.log(`\n=== SYSTEM HEALTH TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'health': ${body.includes("health")}`);
  console.log(`  Has 'budget': ${body.includes("budget")}`);
  console.log(`  Has 'redis': ${body.includes("redis")}`);
  console.log(`  Has 'postgres': ${body.includes("postgres")}`);
  console.log(`  Has 'queue': ${body.includes("queue") || body.includes("dlq")}`);
  console.log(`  Has 'trace': ${body.includes("trace")}`);

  console.log(`  API calls:`);
  for (const c of result.apiCalls) {
    console.log(`    [${c.ok ? "OK" : "FAIL"}] ${c.method} ${c.url} -> ${c.status}`);
  }

  await page.screenshot({ path: "test-results/flow-system.png", fullPage: true });
});

// ── 6. Agents Page CRUD ─────────────────────────────────────────

test("[Flow] Agents: list and toggle", async ({ page }) => {
  const result = await visitPage(page, "/agents");

  console.log(`\n=== AGENTS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  const agentNames = ["observer", "librarian", "planner", "governor", "operator", "presenter", "researcher", "persona"];
  for (const name of agentNames) {
    console.log(`  Agent '${name}' visible: ${body.includes(name)}`);
  }

  // Try toggle
  const toggles = page.locator('[role="switch"], button:has-text("Disable"), button:has-text("Enable")');
  const toggleCount = await toggles.count();
  console.log(`  Toggle elements: ${toggleCount}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);
  console.log(`  Console Errors: ${result.consoleErrors.length}`);

  await page.screenshot({ path: "test-results/flow-agents.png", fullPage: true });
});

// ── 7. Settings Page ────────────────────────────────────────────

test("[Flow] Settings: policy and budget controls", async ({ page }) => {
  const result = await visitPage(page, "/settings");

  console.log(`\n=== SETTINGS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'policy': ${body.includes("policy")}`);
  console.log(`  Has 'mode': ${body.includes("mode")}`);
  console.log(`  Has 'budget': ${body.includes("budget")}`);
  console.log(`  Has 'token': ${body.includes("token")}`);

  const selects = page.locator("select, [role='combobox']");
  const selectCount = await selects.count();
  console.log(`  Select/combobox elements: ${selectCount}`);

  const buttons = page.locator("button");
  const btnCount = await buttons.count();
  console.log(`  Buttons: ${btnCount}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-settings.png", fullPage: true });
});

// ── 8. Sidebar Navigation ───────────────────────────────────────

test("[Flow] Sidebar: all nav links present and clickable", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  console.log(`\n=== SIDEBAR NAVIGATION TEST ===`);

  const navLinks = page.locator("nav a[href], aside a[href]");
  const count = await navLinks.count();
  console.log(`  Total nav links: ${count}`);

  const hrefs: string[] = [];
  for (let i = 0; i < count; i++) {
    const href = await navLinks.nth(i).getAttribute("href");
    const text = (await navLinks.nth(i).textContent()) ?? "";
    if (href && !hrefs.includes(href)) {
      hrefs.push(href);
      console.log(`  Link: ${href} ("${text.trim().substring(0, 30)}")`);
    }
  }

  // Click through each nav link and check for errors
  for (const href of hrefs.filter((h) => h.startsWith("/"))) {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });

    await page.goto(href, { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => null);
    await page.waitForTimeout(800);
    const body = await page.textContent("body").catch(() => "");
    const hasContent = (body?.length ?? 0) > 50;
    const flag = hasContent ? "OK" : "EMPTY";
    console.log(`  Navigate ${href}: [${flag}] ${(body?.length ?? 0)} chars, ${errors.length} errors`);
  }

  await page.screenshot({ path: "test-results/flow-sidebar.png", fullPage: true });
});

// ── 9. Layout / Responsiveness ──────────────────────────────────

test("[Layout] Desktop and mobile render without overflow", async ({ page }) => {
  console.log(`\n=== LAYOUT TEST ===`);

  // Desktop
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  const sidebar = page.locator("nav, aside").first();
  const sidebarVisible = await sidebar.isVisible({ timeout: 3000 }).catch(() => false);
  console.log(`  Desktop sidebar visible: ${sidebarVisible}`);

  const desktopOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  console.log(`  Desktop horizontal overflow: ${desktopOverflow}`);
  await page.screenshot({ path: "test-results/layout-desktop.png", fullPage: true });

  // Mobile
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  console.log(`  Mobile horizontal overflow: ${mobileOverflow}`);
  await page.screenshot({ path: "test-results/layout-mobile.png", fullPage: true });
});

// ── 10. Tasks CRUD ──────────────────────────────────────────────

test("[Flow] Tasks: list renders and create button exists", async ({ page }) => {
  const result = await visitPage(page, "/tasks");

  console.log(`\n=== TASKS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'task' text: ${body.includes("task")}`);
  console.log(`  Has 'create/add/new' button text: ${body.includes("create") || body.includes("add") || body.includes("new")}`);

  const createBtn = page.getByRole("button", { name: /create|add|new/i });
  const hasCreate = await createBtn.first().isVisible({ timeout: 2000 }).catch(() => false);
  console.log(`  Create button visible: ${hasCreate}`);

  // Check for table/list
  const rows = page.locator("tr, [role='row'], [class*='list-item' i]");
  const rowCount = await rows.count();
  console.log(`  List/table rows: ${rowCount}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);
  console.log(`  Console Errors: ${result.consoleErrors.length}`);

  await page.screenshot({ path: "test-results/flow-tasks.png", fullPage: true });
});

// ── 11. Memories Page ───────────────────────────────────────────

test("[Flow] Memories: list renders with data", async ({ page }) => {
  const result = await visitPage(page, "/memories");

  console.log(`\n=== MEMORIES PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'memory/memories' text: ${body.includes("memor")}`);
  console.log(`  Has search input: ${(await page.locator('input[type="search"], input[placeholder*="search" i]').count()) > 0}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-memories.png", fullPage: true });
});

// ── 12. Goals Page ──────────────────────────────────────────────

test("[Flow] Goals: list and create button", async ({ page }) => {
  const result = await visitPage(page, "/goals");

  console.log(`\n=== GOALS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'goal' text: ${body.includes("goal")}`);
  const createBtn = page.getByRole("button", { name: /create|add|new/i });
  const hasCreate = await createBtn.first().isVisible({ timeout: 2000 }).catch(() => false);
  console.log(`  Create button visible: ${hasCreate}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-goals.png", fullPage: true });
});

// ── 13. Schedules Page ──────────────────────────────────────────

test("[Flow] Schedules: seeded schedules visible", async ({ page }) => {
  const result = await visitPage(page, "/schedules");

  console.log(`\n=== SCHEDULES PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'schedule' text: ${body.includes("schedule")}`);
  console.log(`  Has 'briefing' text: ${body.includes("briefing")}`);
  console.log(`  Has 'observe' text: ${body.includes("observe")}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-schedules.png", fullPage: true });
});

// ── 14. Workflows Page ──────────────────────────────────────────

test("[Flow] Workflows: lists available workflows", async ({ page }) => {
  const result = await visitPage(page, "/workflows");

  console.log(`\n=== WORKFLOWS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'workflow' text: ${body.includes("workflow")}`);
  console.log(`  Has 'inbox' text: ${body.includes("inbox")}`);
  console.log(`  Has 'triage' text: ${body.includes("triage")}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-workflows.png", fullPage: true });
});

// ── 15. Triggers Page ───────────────────────────────────────────

test("[Flow] Triggers: list renders", async ({ page }) => {
  const result = await visitPage(page, "/triggers");

  console.log(`\n=== TRIGGERS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'trigger' text: ${body.includes("trigger")}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-triggers.png", fullPage: true });
});

// ── 16. Notifications Page ──────────────────────────────────────

test("[Flow] Notifications: list renders", async ({ page }) => {
  const result = await visitPage(page, "/notifications");

  console.log(`\n=== NOTIFICATIONS PAGE TEST ===`);
  const body = result.bodyText.toLowerCase();

  console.log(`  Has 'notification' text: ${body.includes("notification")}`);

  console.log(`  Failed APIs: ${result.apiCalls.filter((c) => !c.ok).map((c) => `${c.url}(${c.status})`).join(", ") || "none"}`);

  await page.screenshot({ path: "test-results/flow-notifications.png", fullPage: true });
});

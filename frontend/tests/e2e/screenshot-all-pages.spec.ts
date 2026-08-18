import { test, type Page } from "@playwright/test";
import path from "path";

const SCREENSHOT_DIR = path.join(__dirname, "..", "screenshots");

const DEMO_USER = {
  user_id: "usr_01KM2EMPNB8WYN2E2S286DJ52J",
  email: "founder@muldro.dev",
  display_name: "Demo Founder",
};
const DEMO_TOKEN = "demo-session-token-for-muldro-ui-dev";

async function loginAsDemo(page: Page) {
  await page.goto("/login");
  await page.evaluate(
    ({ token, user }) => {
      localStorage.setItem("muldro_auth_token", token);
      localStorage.setItem("muldro_auth_user", JSON.stringify(user));
    },
    { token: DEMO_TOKEN, user: DEMO_USER }
  );
}

async function screenshot(page: Page, name: string) {
  // Wait for network to settle and animations to finish
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(800);
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${name}.png`),
    fullPage: true,
  });
}

async function screenshotViewport(page: Page, name: string) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(500);
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${name}.png`),
    fullPage: false,
  });
}

// ── Login page (before auth) ─────────────────────────────────

test("login page", async ({ page }) => {
  await page.goto("/login");
  await page.waitForTimeout(500);
  await screenshot(page, "01-login");
});

// ── All authenticated pages ──────────────────────────────────

test.describe("authenticated pages", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsDemo(page);
  });

  // ── Dashboard ──────────────────────────────────────────────

  test("dashboard", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("h1");
    await screenshot(page, "02-dashboard");
  });

  // ── Chat page ──────────────────────────────────────────────

  test("chat page", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForSelector("h1, h2");
    await screenshot(page, "03-chat");
  });

  // ── Briefings ──────────────────────────────────────────────

  test("briefings page", async ({ page }) => {
    await page.goto("/briefings");
    await page.waitForSelector("h1");
    await screenshot(page, "04-briefings");
  });

  // ── Conversations ──────────────────────────────────────────

  test("conversations page", async ({ page }) => {
    await page.goto("/conversations");
    await page.waitForSelector("h1");
    await screenshot(page, "05-conversations");

    // Click first conversation if exists
    const firstConv = page.locator("a[href^='/conversations'], [class*='cursor-pointer']").first();
    if (await firstConv.isVisible().catch(() => false)) {
      await firstConv.click();
      await page.waitForTimeout(800);
      await screenshot(page, "05b-conversation-detail");
    }
  });

  // ── Search ─────────────────────────────────────────────────

  test("search page", async ({ page }) => {
    await page.goto("/search");
    await page.waitForSelector("h1");
    await screenshot(page, "06-search");

    // Try searching
    const input = page.locator("input[type='text'], input[type='search']").first();
    if (await input.isVisible().catch(() => false)) {
      await input.fill("investor update");
      // Press enter or click search button
      await input.press("Enter").catch(() => {});
      await page.waitForTimeout(1500);
      await screenshot(page, "06b-search-results");
    }
  });

  // ── Approvals with tabs ────────────────────────────────────

  test("approvals page + tabs", async ({ page }) => {
    await page.goto("/approvals");
    await page.waitForSelector("h1");
    await screenshot(page, "07-approvals-pending");

    // Click each tab
    const tabs = ["Approved", "Rejected", "All"];
    for (const tab of tabs) {
      const btn = page.getByRole("tab", { name: tab });
      if (await btn.isVisible().catch(() => false)) {
        await btn.click();
        await page.waitForTimeout(600);
        await screenshot(page, `07-approvals-${tab.toLowerCase()}`);
      }
    }

    // Click first approval card for detail modal
    const card = page.locator("[class*='cursor-pointer']").first();
    if (await card.isVisible().catch(() => false)) {
      await card.click();
      await page.waitForTimeout(600);
      await screenshotViewport(page, "07-approval-detail-modal");
      // Close modal
      await page.keyboard.press("Escape");
    }
  });

  // ── Tasks with tabs ────────────────────────────────────────

  test("tasks page + tabs", async ({ page }) => {
    await page.goto("/tasks");
    await page.waitForSelector("h1");
    await screenshot(page, "08-tasks");

    // Click filter tabs if they exist
    const tabButtons = page.locator("button[role='tab']");
    const count = await tabButtons.count();
    for (let i = 0; i < Math.min(count, 4); i++) {
      await tabButtons.nth(i).click();
      await page.waitForTimeout(500);
      const label = await tabButtons.nth(i).textContent();
      await screenshot(page, `08-tasks-tab-${label?.trim().toLowerCase().replace(/\s+/g, "-") || i}`);
    }

    // Click first task for detail
    const taskLink = page.locator("a[href^='/tasks/']").first();
    if (await taskLink.isVisible().catch(() => false)) {
      await taskLink.click();
      await page.waitForSelector("h1");
      await screenshot(page, "08b-task-detail");
    }
  });

  // ── Schedules ──────────────────────────────────────────────

  test("schedules page", async ({ page }) => {
    await page.goto("/schedules");
    await page.waitForSelector("h1");
    await screenshot(page, "09-schedules");
  });

  // ── Goals ──────────────────────────────────────────────────

  test("goals page", async ({ page }) => {
    await page.goto("/goals");
    await page.waitForSelector("h1");
    await screenshot(page, "10-goals");
  });

  // ── Notifications ──────────────────────────────────────────

  test("notifications page", async ({ page }) => {
    page.on("pageerror", () => {}); // Suppress page crash
    await page.goto("/notifications");
    await page.waitForTimeout(2000);
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "11-notifications.png"),
      fullPage: true,
    }).catch(() => {});
  });

  // ── Integrations ─────────────────────────────────────────────

  test("integrations page", async ({ page }) => {
    await page.goto("/integrations");
    await page.waitForSelector("h1");
    await screenshot(page, "12-integrations");
  });

  // ── Entities ───────────────────────────────────────────────

  test("entities page", async ({ page }) => {
    await page.goto("/entities");
    await page.waitForSelector("h1");
    await screenshot(page, "13-entities");

    // Click filter tabs if they exist
    const tabButtons = page.locator("button[role='tab']");
    const count = await tabButtons.count();
    for (let i = 1; i < Math.min(count, 4); i++) {
      await tabButtons.nth(i).click();
      await page.waitForTimeout(500);
      const label = await tabButtons.nth(i).textContent();
      await screenshot(page, `13-entities-${label?.trim().toLowerCase() || i}`);
    }
  });

  // ── Memories ───────────────────────────────────────────────

  test("memories page", async ({ page }) => {
    await page.goto("/memories");
    await page.waitForSelector("h1");
    await screenshot(page, "14-memories");

    // Click memory type tabs if present
    const tabButtons = page.locator("button[role='tab']");
    const count = await tabButtons.count();
    for (let i = 1; i < Math.min(count, 4); i++) {
      await tabButtons.nth(i).click();
      await page.waitForTimeout(500);
      const label = await tabButtons.nth(i).textContent();
      await screenshot(page, `14-memories-${label?.trim().toLowerCase() || i}`);
    }
  });

  // ── Executions ─────────────────────────────────────────────

  test("executions page", async ({ page }) => {
    await page.goto("/executions");
    await page.waitForSelector("h1");
    await screenshot(page, "15-executions");
  });

  // ── Runs ───────────────────────────────────────────────────

  test("runs page", async ({ page }) => {
    await page.goto("/runs");
    await page.waitForSelector("h1");
    await screenshot(page, "16-runs");

    // Click first run for detail
    const runLink = page.locator("a[href^='/runs/']").first();
    if (await runLink.isVisible().catch(() => false)) {
      await runLink.click();
      await page.waitForSelector("h1");
      await screenshot(page, "16b-run-detail");
    }
  });

  // ── Triggers ───────────────────────────────────────────────

  test("triggers page", async ({ page }) => {
    await page.goto("/triggers");
    await page.waitForSelector("h1");
    await screenshot(page, "17-triggers");
  });

  // ── Workflows ──────────────────────────────────────────────

  test("workflows page", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForSelector("h1");
    await screenshot(page, "18-workflows");
  });

  // ── System Health ──────────────────────────────────────────

  test("system health page", async ({ page }) => {
    await page.goto("/system");
    await page.waitForSelector("h1");
    await screenshot(page, "19-system-health");
  });

  // ── Traces ─────────────────────────────────────────────────

  test("traces page", async ({ page }) => {
    await page.goto("/traces");
    await page.waitForSelector("h1");
    await screenshot(page, "20-traces");

    // Click filter tabs if present
    const tabButtons = page.locator("button[role='tab']");
    const count = await tabButtons.count();
    for (let i = 1; i < Math.min(count, 3); i++) {
      await tabButtons.nth(i).click();
      await page.waitForTimeout(500);
      const label = await tabButtons.nth(i).textContent();
      await screenshot(page, `20-traces-${label?.trim().toLowerCase() || i}`);
    }
  });

  // ── Agents ─────────────────────────────────────────────────

  test("agents page", async ({ page }) => {
    await page.goto("/agents");
    await page.waitForSelector("h1");
    await screenshot(page, "21-agents");
  });

  // ── Routes ─────────────────────────────────────────────────

  test("routes page", async ({ page }) => {
    await page.goto("/routes");
    await page.waitForSelector("h1");
    await screenshot(page, "22-routes");
  });

  // ── Settings ───────────────────────────────────────────────

  test("settings page", async ({ page }) => {
    await page.goto("/settings");
    await page.waitForSelector("h1");
    await screenshot(page, "23-settings");

    // Click setting tabs/sections if present
    const tabButtons = page.locator("button[role='tab']");
    const count = await tabButtons.count();
    for (let i = 1; i < Math.min(count, 4); i++) {
      await tabButtons.nth(i).click();
      await page.waitForTimeout(500);
      const label = await tabButtons.nth(i).textContent();
      await screenshot(page, `23-settings-${label?.trim().toLowerCase().replace(/\s+/g, "-") || i}`);
    }
  });

  // ── Sidebar: expanded state ────────────────────────────────

  test("sidebar expanded + light theme", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("h1");

    // Dismiss Next.js dev overlay if present
    await page.evaluate(() => {
      document.querySelectorAll("nextjs-portal").forEach((el) => el.remove());
    });

    // Click the sidebar toggle to expand
    const toggle = page.locator("button[aria-label='Expand sidebar'], button[aria-label='Collapse sidebar']").first();
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click();
      await page.waitForTimeout(400);
      await screenshotViewport(page, "24-sidebar-expanded");
    }

    // Switch to light theme via JS
    await page.evaluate(() => {
      localStorage.setItem("muldro_theme", "light");
      document.documentElement.setAttribute("data-theme", "light");
    });
    await page.waitForTimeout(400);
    await screenshotViewport(page, "25-light-theme");

    // Switch back to dark
    await page.evaluate(() => {
      localStorage.setItem("muldro_theme", "dark");
      document.documentElement.setAttribute("data-theme", "dark");
    });
    await page.waitForTimeout(400);
  });

  // ── Mobile viewport ────────────────────────────────────────

  test("mobile viewport - dashboard", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.waitForSelector("h1");
    await screenshot(page, "26-mobile-dashboard");

    // Open mobile menu
    const hamburger = page.locator("button[aria-label='Toggle navigation']");
    if (await hamburger.isVisible().catch(() => false)) {
      await hamburger.click();
      await page.waitForTimeout(400);
      await screenshotViewport(page, "26b-mobile-sidebar-open");
    }
  });

  test("mobile viewport - approvals", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/approvals");
    await page.waitForSelector("h1");
    await screenshot(page, "27-mobile-approvals");
  });

  test("mobile viewport - chat", async ({ page }) => {
    page.on("pageerror", () => {});
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/chat");
    await page.waitForTimeout(2000);
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "28-mobile-chat.png"),
      fullPage: false,
    }).catch(() => {});
  });

  // ── Tablet viewport ────────────────────────────────────────

  test("tablet viewport - dashboard", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto("/");
    await page.waitForSelector("h1");
    await screenshot(page, "29-tablet-dashboard");
  });
});

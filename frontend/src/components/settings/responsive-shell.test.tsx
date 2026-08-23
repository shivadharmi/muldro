/**
 * §9.10 — the settings SHELL below and above the `sm` breakpoint.
 *
 * Split from `responsive.test.tsx` because the shell is the one piece that
 * cannot be rendered from plain props: it reaches for the auth context, the
 * modal store and every tab's API surface, so it needs the mock block below.
 * The contract it pins is the top four rows of §9.10's table — the full sheet,
 * the rail as the root view, the pushed header, and the body's gutter — which
 * the component specs cannot see because none of them mounts the shell.
 *
 * Like its sibling, this asserts the CLASS CONTRACT and not a rendering: jsdom
 * evaluates no media query and computes no layout, so **the pixel result is
 * verified in a browser**. What is checked here is that each §9.10 metric is the
 * unprefixed utility with the desktop value on `sm:` — the direction, which is
 * the half a diff makes easy to get backwards.
 */

import { render, screen } from "@testing-library/react";
import { expect, test, vi, beforeEach } from "vitest";

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "founder@example.com", display_name: "Founder" },
    logout: vi.fn(),
  }),
}));
vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", resolved: "dark", setTheme: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn().mockResolvedValue({ mode: "approval_required" }),
  setPolicyMode: vi.fn().mockResolvedValue({}),
  fetchWorkspaceDefaultPermissionMode: vi
    .fn()
    .mockResolvedValue({ default_permission_mode: "auto" }),
  setWorkspaceDefaultPermissionMode: vi.fn().mockResolvedValue({}),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  updateBudgetLimit: vi.fn().mockResolvedValue({}),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  setTrustCeiling: vi.fn().mockResolvedValue({}),
  resetTrust: vi.fn().mockResolvedValue({}),
  fetchModelCatalog: vi
    .fn()
    .mockResolvedValue({ providers: [], models: [], agents: [] }),
  fetchModelConfig: vi
    .fn()
    .mockResolvedValue({ tiers: [], agent_overrides: [], providers: [], warnings: [] }),
  saveModelConfig: vi.fn().mockResolvedValue({}),
  saveProviderCredential: vi.fn().mockResolvedValue({ status: "valid" }),
  testProviderKey: vi.fn().mockResolvedValue({ status: "valid" }),
  deleteProviderKey: vi.fn().mockResolvedValue({ orphaned_bindings: [] }),
}));

import { SettingsModal } from "./settings-modal";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import {
  MOBILE_WIDTH,
  classesOf,
  expectBaseClass,
  expectSmClass,
  expectTouchTarget,
} from "./responsive-fixtures";

/** Opened straight onto a named tab, which lands PUSHED — the only state in
 *  which the mobile header's back control exists to be asserted. */
beforeEach(() => {
  useSettingsModalStore.setState({ open: true, activeTab: "model" });
});

/**
 * Mount the shell and let `ModelConfigProvider`'s mount fetch settle.
 *
 * The provider loads the catalog on mount, so a synchronous `render` leaves a
 * resolved promise about to set state — React reports that as an un-acted
 * update, on stderr, on every test in the file. `findByRole` runs its poll
 * inside `act`, which adopts the update instead.
 */
async function mountShell(): Promise<void> {
  render(<SettingsModal />);
  await screen.findByRole("dialog");
}

function panel(): HTMLElement {
  // The dialog's own child, not the dialog: the backdrop is the other child and
  // the sheet metrics belong to the panel.
  const dialog = screen.getByRole("dialog");
  const found = dialog.querySelector<HTMLElement>(":scope > div:not([data-testid])");
  expect(found, "the dialog rendered no panel").not.toBeNull();
  return found as HTMLElement;
}

test(`at ${MOBILE_WIDTH}px the panel fills the viewport, and only rounds at \`sm\``, async () => {
  await mountShell();
  const classes = classesOf(panel());

  // Full sheet: the panel takes the whole `inset-0` dialog below `sm`, and only
  // becomes a centred, capped, rounded card above it.
  expect(classes).toContain("w-full");
  expect(classes).toContain("h-full");
  expect(classes).toContain("sm:max-w-4xl");

  // No radius, no border and no shadow below `sm` — all three are `sm:`-only.
  // A rounded corner on a full-bleed sheet shows the page behind it in the gap.
  for (const cls of classes) {
    expect(cls.startsWith("rounded-"), `\`${cls}\` rounds the mobile sheet`).toBe(
      false,
    );
  }
  expect(classes).toContain("sm:rounded-[var(--radius-xl)]");
  expect(classes).toContain("sm:border");

  // Stacked below `sm` (rail above body, one at a time), side by side above it.
  expect(classes).toContain("flex-col");
  expect(classes).toContain("sm:flex-row");
});

test(`at ${MOBILE_WIDTH}px the rail is the root view, full width, not a scroller`, async () => {
  await mountShell();
  const rail = screen.getByRole("navigation", { name: /settings sections/i });
  const classes = classesOf(rail);

  // A push LIST: full width and stacked, never a horizontal strip (defect L4).
  expect(classes).toContain("w-full");
  expect(classes).toContain("flex-col");
  expect(classes).toContain("sm:w-[200px]");
  expect(classes).not.toContain("overflow-x-auto");
  expect(classes).not.toContain("flex-row");
});

test(`at ${MOBILE_WIDTH}px the pushed header's back and close controls are 44px`, async () => {
  await mountShell();

  // `‹ Settings`, the mobile-only way back to the root view.
  const back = screen.getByRole("button", { name: /^settings$/i });
  expectTouchTarget(back, "the pushed header's back control");
  expect(classesOf(back)).toContain("sm:hidden");
  expect(classesOf(back)).toContain("text-j-primary");
  expect(classesOf(back)).toContain("text-[15px]");

  // 44 × 44, square, and only shrinking to a 4px-padded icon at `sm`.
  const close = screen.getByRole("button", { name: /close settings/i });
  expectTouchTarget(close, "the pushed header's close control");
  expect(classesOf(close)).toContain("w-[44px]");
});

test(`at ${MOBILE_WIDTH}px the header title is centred at 16px/600`, async () => {
  await mountShell();
  const title = screen.getByText("Model", { selector: "p" });

  // `text-base` is 16px, and 600 is `font-semibold` — §9.10's pushed title.
  expect(classesOf(title)).toContain("text-base");
  expect(classesOf(title)).toContain("font-semibold");
  expect(classesOf(title)).toContain("sm:text-[15px]");

  // The ALIGNMENT is on the flexing wrapper, not on the paragraph: the title
  // shares the header row with a back control and a close control, and it is
  // the CELL that has to centre between them. Centred below `sm`, left-aligned
  // above it — where the back control is gone and there is nothing to centre
  // against.
  const cell = title.parentElement as HTMLElement;
  expect(classesOf(cell)).toContain("text-center");
  expect(classesOf(cell)).toContain("sm:text-left");
});

test(`at ${MOBILE_WIDTH}px the body is 18px/16px on \`surface-0\``, async () => {
  await mountShell();
  // Reached from the header rather than by a class query: the RAIL is also a
  // `flex-col` scroller, so `.overflow-y-auto` finds it first and the whole
  // test would then assert the wrong element's padding.
  const header = screen.getByRole("button", { name: /close settings/i })
    .parentElement as HTMLElement;
  const el = header.nextElementSibling as HTMLElement;
  expect(el, "the shell rendered no scrolling body").not.toBeNull();
  expect(classesOf(el)).toContain("overflow-y-auto");

  expectBaseClass(el, "py-[18px]", "the body's mobile vertical padding");
  expectBaseClass(el, "px-4", "the body's 16px mobile gutter");
  expectBaseClass(el, "gap-[18px]", "the body's stack gap");
  expectSmClass(el, "px-6", "the body's desktop gutter");

  // Opaque `surface-0` below `sm`: the panel's own `surface-1` fill is a card
  // treatment, and on a full sheet the body is the whole screen.
  expectBaseClass(el, "bg-surface-0", "the body's mobile fill");
  expectSmClass(el, "bg-transparent", "the body deferring to the panel at `sm`");
});

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));
const { setTheme } = vi.hoisted(() => ({ setTheme: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "founder@example.com", display_name: "Founder" },
    logout,
  }),
}));
vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", resolved: "dark", setTheme }),
}));
// Every API function the settings surface can reach, so a stray call from the
// SHELL shows up as a called mock rather than as an unhandled rejection.
vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn().mockResolvedValue({ mode: "approval_required" }),
  setPolicyMode: vi.fn().mockResolvedValue({}),
  fetchWorkspaceDefaultPermissionMode: vi
    .fn()
    .mockResolvedValue({ default_permission_mode: "auto" }),
  setWorkspaceDefaultPermissionMode: vi.fn().mockResolvedValue({}),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  updateBudgetLimit: vi.fn().mockResolvedValue({ daily_limit_usd: 30 }),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  setTrustCeiling: vi.fn().mockResolvedValue({}),
  resetTrust: vi.fn().mockResolvedValue({}),
  fetchModelCatalog: vi
    .fn()
    .mockResolvedValue({ providers: [], models: [], agents: [] }),
  fetchModelConfig: vi.fn().mockResolvedValue({
    tiers: [],
    agent_overrides: [],
    providers: [
      { provider: "anthropic", configured: true },
      { provider: "openai", configured: false },
    ],
    warnings: [],
  }),
  saveModelConfig: vi.fn().mockResolvedValue({}),
  saveProviderCredential: vi.fn().mockResolvedValue({ status: "valid" }),
  testProviderKey: vi.fn().mockResolvedValue({ status: "valid" }),
  deleteProviderKey: vi.fn().mockResolvedValue({ orphaned_bindings: [] }),
}));

import { SettingsModal } from "./settings-modal";
import { SETTINGS_TABS } from "./settings-rail";
import {
  fetchBudget,
  fetchModelCatalog,
  fetchModelConfig,
  fetchPolicyMode,
  fetchTrustDashboard,
} from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";

beforeEach(() => {
  vi.clearAllMocks();
  useSettingsModalStore.setState({ open: true, activeTab: "account" });
});

/** The rail, addressed by its own landmark so a tab's body cannot answer for it. */
function rail() {
  return screen.getByRole("navigation", { name: /settings sections/i });
}

test("does not render when closed", () => {
  useSettingsModalStore.setState({ open: false });
  render(<SettingsModal />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("the rail lists all seven tabs", () => {
  render(<SettingsModal />);
  const items = within(rail()).getAllByRole("button");
  expect(items).toHaveLength(7);
  expect(items.map((b) => b.textContent?.trim())).toEqual(
    SETTINGS_TABS.map((t) => t.label),
  );
});

test("Providers sits directly after Model in the rail", () => {
  render(<SettingsModal />);
  const labels = SETTINGS_TABS.map((t) => t.key);
  expect(labels.indexOf("providers")).toBe(labels.indexOf("model") + 1);
});

test("clicking a rail item sets the store's active tab", async () => {
  render(<SettingsModal />);
  await userEvent.click(
    within(rail()).getByRole("button", { name: /^trust$/i }),
  );
  expect(useSettingsModalStore.getState().activeTab).toBe("trust");
});

test("the dialog is labelled by the visible heading, not by aria-label (A2)", () => {
  render(<SettingsModal />);
  const dialog = screen.getByRole("dialog");
  expect(dialog).not.toHaveAttribute("aria-label");

  const labelledBy = dialog.getAttribute("aria-labelledby");
  expect(labelledBy).toBeTruthy();
  const heading = document.getElementById(labelledBy as string);
  expect(heading?.tagName).toBe("H2");
  expect(heading).toHaveTextContent("Settings");
  expect(dialog).toHaveAccessibleName("Settings");
});

test("focus moves into the dialog on open (A1)", () => {
  const invoker = document.createElement("button");
  document.body.appendChild(invoker);
  invoker.focus();

  render(<SettingsModal />);
  const dialog = screen.getByRole("dialog");
  expect(dialog.contains(document.activeElement)).toBe(true);

  invoker.remove();
});

test("Tab is trapped inside the dialog and wraps in both directions (A1)", () => {
  render(<SettingsModal />);
  const dialog = screen.getByRole("dialog");
  const focusable = Array.from(
    dialog.querySelectorAll<HTMLElement>("button, input, select, a[href]"),
  );
  const first = focusable[0];
  const last = focusable[focusable.length - 1];

  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(document.activeElement).toBe(first);

  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(last);
});

/**
 * jsdom implements `inert` as an inert attribute — it neither blurs nor blocks
 * focus — so both halves of the trap's ordering invariant are invisible by
 * default, and every ordering test here has to reinstate the browser behaviour
 * it depends on. This is the half that makes `focus()` a no-op inside an inert
 * subtree, which is what the RELEASE-before-RESTORE order exists for.
 */
function enforceInertBlocksFocus() {
  const realFocus = HTMLElement.prototype.focus;
  return vi
    .spyOn(HTMLElement.prototype, "focus")
    .mockImplementation(function (this: HTMLElement, options?: FocusOptions) {
      if (this.closest("[inert]")) return;
      realFocus.call(this, options);
    });
}

test("focus returns to the invoking element on close (A1)", () => {
  const inert = enforceInertBlocksFocus();
  const invoker = document.createElement("button");
  document.body.appendChild(invoker);
  invoker.focus();

  try {
    const { rerender } = render(<SettingsModal />);
    expect(document.activeElement).not.toBe(invoker);

    useSettingsModalStore.setState({ open: false });
    rerender(<SettingsModal />);
    // Restoring BEFORE the release would focus into a still-inert ancestor,
    // which a real browser ignores silently.
    expect(document.activeElement).toBe(invoker);
  } finally {
    inert.mockRestore();
    invoker.remove();
  }
});

test("the backdrop is never inerted, and closes the dialog on click", async () => {
  // The trap isolates around the whole dialog, not around the panel. Isolating
  // around the panel makes this backdrop a marked sibling, `inert` kills its
  // onClick, and click-outside-to-close dies with nothing looking wrong.
  render(<SettingsModal />);
  const backdrop = screen.getByTestId("settings-backdrop");
  expect(backdrop).not.toHaveAttribute("inert");
  expect(backdrop).not.toHaveAttribute("aria-hidden");

  await userEvent.click(backdrop);
  expect(useSettingsModalStore.getState().open).toBe(false);
});

test("the panel's own siblings inside the dialog stay reachable", () => {
  // The same invariant stated structurally: nothing inside the dialog is
  // isolated, however deep the panel sits.
  render(<SettingsModal />);
  const dialog = screen.getByRole("dialog");
  expect(dialog.querySelector("[inert]")).toBeNull();
});

test("Esc closes the dialog", () => {
  render(<SettingsModal />);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(useSettingsModalStore.getState().open).toBe(false);
});

test("the close control closes the dialog", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /close settings/i }));
  expect(useSettingsModalStore.getState().open).toBe(false);
});

test("the shell fetches no tab's data for a tab that is not on screen (L5)", async () => {
  render(<SettingsModal />);
  // Account is active. No other tab's endpoints may fire — the shell holds no
  // tab's state, so nothing off-screen has anything to load.
  expect(fetchTrustDashboard).not.toHaveBeenCalled();
  expect(fetchBudget).not.toHaveBeenCalled();
  expect(fetchPolicyMode).not.toHaveBeenCalled();

  await userEvent.click(
    within(rail()).getByRole("button", { name: /^policy$/i }),
  );
  await waitFor(() => expect(fetchPolicyMode).toHaveBeenCalled());
  expect(fetchTrustDashboard).not.toHaveBeenCalled();
  expect(fetchBudget).not.toHaveBeenCalled();
});

test("opening the surface fetches the config for the badge, and no catalog", async () => {
  render(<SettingsModal />);
  await waitFor(() => expect(fetchModelConfig).toHaveBeenCalledTimes(1));
  // The catalog is every provider's models and credential schemas, and only
  // two tabs render it. Most Settings visits are someone changing a theme.
  expect(fetchModelCatalog).not.toHaveBeenCalled();
});

test("the catalog arrives with the first tab that needs it, once", async () => {
  render(<SettingsModal />);
  await waitFor(() => expect(fetchModelConfig).toHaveBeenCalledTimes(1));

  await userEvent.click(within(rail()).getByRole("button", { name: /^model$/i }));
  await waitFor(() => expect(fetchModelCatalog).toHaveBeenCalledTimes(1));

  // Leaving and returning must not re-fetch either: the state is owned above
  // both the rail and the tab, not by the tab that happens to be open.
  await userEvent.click(within(rail()).getByRole("button", { name: /^trust$/i }));
  await userEvent.click(within(rail()).getByRole("button", { name: /^model$/i }));
  expect(fetchModelCatalog).toHaveBeenCalledTimes(1);
  // And the eager fetch is not repeated by the tab's own full load.
  expect(fetchModelConfig).toHaveBeenCalledTimes(1);
});

test("the rail omits the provider count until the config lands, then shows it", async () => {
  render(<SettingsModal />);
  const providers = () =>
    within(rail()).getByRole("button", { name: /providers/i });
  // Nothing has loaded on this first paint: no suffix, and never a bare `0/0`.
  expect(providers().textContent).toBe("Providers");
  // The badge's whole job is to flag Providers BEFORE the user goes there.
  await waitFor(() => expect(providers().textContent).toBe("Providers1/2"));
});

test("the header names the active tab", async () => {
  render(<SettingsModal />);
  await userEvent.click(
    within(rail()).getByRole("button", { name: /^budget$/i }),
  );
  expect(screen.getAllByText("Budget").length).toBeGreaterThan(1);
});

test("the account tab renders the signed-in user", () => {
  render(<SettingsModal />);
  expect(screen.getByText("founder@example.com")).toBeInTheDocument();
});

test("a hidden pane's controls leave the Tab cycle (A1)", () => {
  render(<SettingsModal />);
  const dialog = screen.getByRole("dialog");
  const nav = rail();
  // Below `sm` the rail is `hidden sm:flex`. jsdom loads no Tailwind, so the
  // real `display:none` is applied here directly — a class would be ignored and
  // the assertion would pass against a broken visibility check.
  nav.style.display = "none";

  const visible = Array.from(
    dialog.querySelectorAll<HTMLElement>("button, input, select, a[href]"),
  ).filter((el) => !nav.contains(el));
  const first = visible[0];
  const last = visible[visible.length - 1];

  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  // `display` does not inherit, so checking the button alone would report it
  // visible and wrap onto a rail item nobody can see.
  expect(nav.contains(document.activeElement)).toBe(false);
  expect(document.activeElement).toBe(first);

  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(last);
});

test("Esc that a nested overlay already handled does not close the dialog", () => {
  render(<SettingsModal />);
  const inner = screen.getByRole("button", { name: /close settings/i });
  const claim = (e: KeyboardEvent) => {
    if (e.key === "Escape") e.preventDefault();
  };
  inner.addEventListener("keydown", claim);

  fireEvent.keyDown(inner, { key: "Escape" });
  expect(useSettingsModalStore.getState().open).toBe(true);

  inner.removeEventListener("keydown", claim);
  fireEvent.keyDown(inner, { key: "Escape" });
  expect(useSettingsModalStore.getState().open).toBe(false);
});

test("focus is captured BEFORE the background goes inert (A1)", () => {
  const invoker = document.createElement("button");
  document.body.appendChild(invoker);
  invoker.focus();

  // jsdom implements neither `inert` nor the blur it causes, so the ordering
  // this guards is invisible by default. Per HTML, a node becoming inert
  // unfocuses a focused descendant (Blink clears `focused_element_`) — and the
  // control that opened the dialog is inside the region about to go inert.
  // Patching `setAttribute` reproduces that synchronously; a MutationObserver
  // could not, because its callback is a microtask and would land after the
  // capture either way.
  const realSetAttribute = Element.prototype.setAttribute;
  const patched = vi
    .spyOn(Element.prototype, "setAttribute")
    .mockImplementation(function (this: Element, name: string, value: string) {
      realSetAttribute.call(this, name, value);
      if (name !== "inert") return;
      const active = document.activeElement;
      if (active instanceof HTMLElement && this.contains(active)) active.blur();
    });

  try {
    const { rerender } = render(<SettingsModal />);
    // Read after isolation: the invoker really was blurred, so a capture that
    // ran second would have recorded nothing.
    expect(document.activeElement).not.toBe(invoker);

    useSettingsModalStore.setState({ open: false });
    rerender(<SettingsModal />);
    expect(document.activeElement).toBe(invoker);
  } finally {
    patched.mockRestore();
    invoker.remove();
  }
});

test("the page behind the dialog is inert, but live regions are not", () => {
  const background = document.createElement("div");
  const toasts = document.createElement("div");
  toasts.setAttribute("role", "status");
  document.body.append(background, toasts);

  const { unmount } = render(<SettingsModal />);
  expect(background).toHaveAttribute("inert");
  expect(background).toHaveAttribute("aria-hidden", "true");
  // Settings raises its own toasts; inerting them would silence the dialog.
  expect(toasts).not.toHaveAttribute("inert");

  unmount();
  expect(background).not.toHaveAttribute("inert");
  expect(background).not.toHaveAttribute("aria-hidden");

  background.remove();
  toasts.remove();
});

test("opening straight onto a named tab lands pushed, not on the rail list", () => {
  useSettingsModalStore.setState({ open: true, activeTab: "model" });
  render(<SettingsModal />);
  // Below `sm` the rail is the root view; a deep link must push past it.
  expect(rail().className).toContain("hidden");
});

test("opening with no tab lands on the rail list", () => {
  render(<SettingsModal />);
  expect(rail().className).not.toContain("hidden");
});

test("the rail's §9.4 width is encoded in one place", () => {
  render(<SettingsModal />);
  expect(rail().className.match(/sm:w-\[200px\]/g)).toHaveLength(1);
});

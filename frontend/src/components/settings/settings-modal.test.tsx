import { fireEvent, render, screen, within } from "@testing-library/react";
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
    providers: [],
    warnings: [],
  }),
  saveModelConfig: vi.fn().mockResolvedValue({}),
  saveProviderCredential: vi.fn().mockResolvedValue({ status: "valid" }),
  testProviderKey: vi.fn().mockResolvedValue({ status: "valid" }),
  deleteProviderKey: vi.fn().mockResolvedValue({ orphaned_bindings: [] }),
}));

import { SettingsModal } from "./settings-modal";
import { SETTINGS_TABS } from "./settings-rail";
import { fetchModelCatalog, fetchModelConfig } from "@/lib/api";
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

test("focus returns to the invoking element on close (A1)", () => {
  const invoker = document.createElement("button");
  document.body.appendChild(invoker);
  invoker.focus();

  const { rerender } = render(<SettingsModal />);
  expect(document.activeElement).not.toBe(invoker);

  useSettingsModalStore.setState({ open: false });
  rerender(<SettingsModal />);
  expect(document.activeElement).toBe(invoker);

  invoker.remove();
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

test("the shell fetches no model config for a non-model tab (L5)", async () => {
  render(<SettingsModal />);
  // Account is active; the rail's provider suffix must not have provoked a load.
  await userEvent.click(
    within(rail()).getByRole("button", { name: /^policy$/i }),
  );
  expect(fetchModelCatalog).not.toHaveBeenCalled();
  expect(fetchModelConfig).not.toHaveBeenCalled();
});

test("the rail omits the provider count while the config is unloaded", () => {
  render(<SettingsModal />);
  const providers = within(rail()).getByRole("button", { name: /providers/i });
  expect(providers.textContent).toBe("Providers");
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

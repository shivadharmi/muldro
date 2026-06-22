import { render, screen, waitFor } from "@testing-library/react";
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
vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn().mockResolvedValue({ mode: "approval_required" }),
  setPolicyMode: vi.fn().mockResolvedValue({}),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  updateBudgetLimit: vi.fn().mockResolvedValue({ daily_limit_usd: 30 }),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  setTrustCeiling: vi.fn().mockResolvedValue({}),
  resetTrust: vi.fn().mockResolvedValue({}),
}));

import { SettingsModal } from "./settings-modal";
import {
  setPolicyMode,
  updateBudgetLimit,
  fetchTrustDashboard,
} from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";

beforeEach(() => {
  vi.clearAllMocks();
  useSettingsModalStore.setState({ open: true, activeTab: "account" });
});

test("renders the five settings tabs", () => {
  render(<SettingsModal />);
  expect(screen.getByRole("button", { name: /^account$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^preferences$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^policy$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^budget$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^trust$/i })).toBeInTheDocument();
});

test("does not render when closed", () => {
  useSettingsModalStore.setState({ open: false });
  render(<SettingsModal />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("account tab shows the user email by default", () => {
  render(<SettingsModal />);
  expect(screen.getByText("founder@example.com")).toBeInTheDocument();
});

test("preferences tab exposes the theme control", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /^preferences$/i }));
  await userEvent.click(screen.getByRole("button", { name: /light/i }));
  expect(setTheme).toHaveBeenCalledWith("light");
});

test("policy tab shows posture options and saves", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /^policy$/i }));
  expect(screen.getByText("Full Auto")).toBeInTheDocument();
  await userEvent.click(screen.getByText("Full Auto"));
  await waitFor(() => expect(setPolicyMode).toHaveBeenCalledWith("full_auto"));
});

test("trust tab loads the dashboard on open", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /^trust$/i }));
  await waitFor(() => expect(fetchTrustDashboard).toHaveBeenCalled());
});

test("budget tab edits the daily limit", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /^budget$/i }));
  await userEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("spinbutton");
  await userEvent.clear(input);
  await userEvent.type(input, "30");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(updateBudgetLimit).toHaveBeenCalledWith(30));
});

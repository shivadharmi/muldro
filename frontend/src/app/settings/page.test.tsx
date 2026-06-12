import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "founder@example.com", display_name: "Founder" },
    logout,
  }),
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

import SettingsPage from "./page";
import {
  setPolicyMode,
  updateBudgetLimit,
  fetchTrustDashboard,
} from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

test("renders the four settings tabs", () => {
  render(<SettingsPage />);
  expect(screen.getByRole("tab", { name: /account/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /policy/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /trust/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /budget/i })).toBeInTheDocument();
});

test("account tab shows the user email by default", () => {
  render(<SettingsPage />);
  expect(screen.getByText("founder@example.com")).toBeInTheDocument();
});

test("selecting a policy mode calls setPolicyMode", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /policy/i }));
  await userEvent.click(screen.getByText("Full Auto"));
  await waitFor(() => expect(setPolicyMode).toHaveBeenCalledWith("full_auto"));
});

test("opening the trust tab loads the trust dashboard", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /trust/i }));
  await waitFor(() => expect(fetchTrustDashboard).toHaveBeenCalled());
});

test("editing the budget calls updateBudgetLimit", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /budget/i }));
  await userEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("spinbutton");
  await userEvent.clear(input);
  await userEvent.type(input, "30");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(updateBudgetLimit).toHaveBeenCalledWith(30));
});

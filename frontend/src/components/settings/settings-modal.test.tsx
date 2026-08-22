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
  fetchWorkspaceDefaultPermissionMode: vi.fn().mockResolvedValue({ default_permission_mode: "auto" }),
  setWorkspaceDefaultPermissionMode: vi.fn().mockResolvedValue({ default_permission_mode: "ask" }),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  updateBudgetLimit: vi.fn().mockResolvedValue({ daily_limit_usd: 30 }),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  setTrustCeiling: vi.fn().mockResolvedValue({}),
  resetTrust: vi.fn().mockResolvedValue({}),
  fetchModelCatalog: vi.fn().mockResolvedValue({
    providers: [
      {
        provider: "anthropic",
        display_name: "Anthropic",
        auth_kind: "api_key",
        credential_fields: [],
        model_count: 1,
        docs_url: null,
      },
    ],
    models: [
      {
        provider: "anthropic",
        model_id: "claude-sonnet-4-6",
        display_name: "Claude Sonnet 4.6",
        thinking_style: "anthropic_legacy",
        accepts_temperature: true,
        suggested_tier: "balanced",
        context_window: 200000,
        input_cost_per_1k: 0.003,
        output_cost_per_1k: 0.015,
        supports_prompt_cache: true,
      },
    ],
    agents: [{ name: "planner", display_name: "Planner", tier: "reasoning" }],
  }),
  fetchModelConfig: vi.fn().mockResolvedValue({
    tiers: [
      {
        scope_type: "tier",
        scope_key: "balanced",
        provider: "anthropic",
        model_id: "claude-sonnet-4-6",
        effort: "medium",
        max_tokens: 4096,
        temperature: null,
      },
    ],
    agent_overrides: [],
    providers: [
      {
        provider: "anthropic",
        configured: true,
        status: "valid",
        source: "workspace",
        base_url: null,
        extra_config_public: {},
        extra_config_secret_keys: [],
        catalogued: true,
      },
    ],
    warnings: [],
  }),
  saveModelConfig: vi.fn().mockResolvedValue({}),
  saveProviderCredential: vi.fn().mockResolvedValue({ status: "valid" }),
  testProviderKey: vi.fn().mockResolvedValue({ status: "valid" }),
  deleteProviderKey: vi.fn().mockResolvedValue({
    status: {
      provider: "anthropic",
      configured: false,
      status: "unconfigured",
      source: "none",
      base_url: null,
      extra_config_public: {},
      extra_config_secret_keys: [],
      catalogued: true,
    },
    orphaned_bindings: [],
  }),
}));

import { SettingsModal } from "./settings-modal";
import {
  setPolicyMode,
  updateBudgetLimit,
  fetchTrustDashboard,
  fetchModelConfig,
} from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";

beforeEach(() => {
  vi.clearAllMocks();
  useSettingsModalStore.setState({ open: true, activeTab: "account" });
});

// Named exhaustively rather than counted: the previous version asserted five of
// them and was already blind to Model, so a tab could vanish entirely and stay green.
const TAB_LABELS = [
  "account",
  "preferences",
  "policy",
  "budget",
  "trust",
  "filters",
  "model",
];

test("renders every settings tab", () => {
  render(<SettingsModal />);
  for (const label of TAB_LABELS) {
    expect(
      screen.getByRole("button", { name: new RegExp(`^${label}$`, "i") }),
    ).toBeInTheDocument();
  }
  const nav = screen.getByRole("dialog").querySelector("nav");
  expect(nav?.querySelectorAll("button")).toHaveLength(TAB_LABELS.length);
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

test("model tab loads the config on open", async () => {
  render(<SettingsModal />);
  await userEvent.click(screen.getByRole("button", { name: /^model$/i }));
  await waitFor(() => expect(fetchModelConfig).toHaveBeenCalled());
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

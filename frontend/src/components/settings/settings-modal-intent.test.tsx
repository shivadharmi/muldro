import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

import type { ModelCatalog, ModelConfig, ProviderStatus } from "@/lib/types";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
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
  fetchWorkspaceDefaultPermissionMode: vi
    .fn()
    .mockResolvedValue({ default_permission_mode: "auto" }),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  fetchModelCatalog: vi.fn(),
  fetchModelConfig: vi.fn(),
  saveModelConfig: vi.fn().mockResolvedValue({}),
  saveProviderCredential: vi.fn().mockResolvedValue({ status: "valid" }),
  testProviderKey: vi.fn().mockResolvedValue({ status: "valid" }),
  deleteProviderKey: vi.fn().mockResolvedValue({ orphaned_bindings: [] }),
}));

import { fetchModelCatalog, fetchModelConfig } from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import { SettingsModal } from "./settings-modal";

/**
 * The Connect→Providers trip through the REAL shell.
 *
 * Every other test of this feature mounts `ProvidersTab` directly, which quietly
 * assumes the thing that actually makes the intent one-shot: `TabBody` switches
 * on `activeTab` and returns a different component type, so the tab UNMOUNTS on
 * leave and remounts on return. Keep both tabs mounted — a plausible "preserve
 * the search box and the scroll position" refactor — and `openProviderFor` would
 * set an intent nothing ever reads, with all six of those tests still green.
 * This is the one that would fail.
 */

const catalog: ModelCatalog = {
  providers: [
    {
      provider: "anthropic",
      display_name: "Anthropic",
      auth_kind: "api_key",
      credential_fields: [
        {
          key: "api_key",
          label: "Anthropic API key",
          kind: "secret",
          required: true,
          placeholder: null,
        },
      ],
      model_count: 1,
      docs_url: null,
    },
    {
      provider: "groq",
      display_name: "Groq",
      auth_kind: "api_key",
      credential_fields: [
        {
          key: "api_key",
          label: "Groq API key",
          kind: "secret",
          required: true,
          placeholder: null,
        },
      ],
      model_count: 1,
      docs_url: null,
    },
  ],
  models: [
    {
      provider: "groq",
      model_id: "llama-3.3-70b",
      display_name: "Llama 3.3 70B",
      thinking_style: "none",
      accepts_temperature: true,
      suggested_tier: "fast",
      context_window: 128000,
      input_cost_per_1k: 0.0006,
      output_cost_per_1k: 0.0008,
      supports_prompt_cache: false,
    },
  ],
  agents: [{ name: "persona", display_name: "Persona", tier: "fast" }],
};

function status(provider: string, configured: boolean): ProviderStatus {
  return {
    provider,
    configured,
    status: configured ? "valid" : "unconfigured",
    source: configured ? "workspace" : "none",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
  };
}

/** Fast is bound to Groq, and Groq resolves no credential — §9.6's warned card,
 *  the one surface that offers `Connect {provider}`. */
const config: ModelConfig = {
  tiers: [
    {
      scope_type: "tier",
      scope_key: "fast",
      provider: "groq",
      model_id: "llama-3.3-70b",
      effort: "low",
      max_tokens: 2000,
      temperature: null,
    },
  ],
  agent_overrides: [],
  providers: [status("anthropic", true), status("groq", false)],
  warnings: [
    {
      scope_type: "tier",
      scope_key: "fast",
      provider: "groq",
      code: "provider_not_configured",
      message: "Groq is not connected.",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModelCatalog).mockResolvedValue(catalog);
  vi.mocked(fetchModelConfig).mockResolvedValue(config);
  useSettingsModalStore.setState({
    open: true,
    activeTab: "model",
    pendingProvider: null,
  });
});

const rail = () =>
  screen.getByRole("navigation", { name: /settings sections/i });

async function openWarnedModelTab() {
  render(<SettingsModal />);
  return userEvent.click(await screen.findByRole("button", { name: "Connect Groq" }));
}

test("Connect on a warned tier lands on that provider's row, open and explained", async () => {
  await openWarnedModelTab();

  // The row is OPEN — the primary action reads Cancel only while it is.
  expect(await screen.findByRole("button", { name: "Cancel Groq" })).toBeTruthy();
  expect(screen.getByLabelText("Groq API key")).toBeTruthy();
  expect(screen.getByText("Needed by the Fast tier")).toBeTruthy();
  // …and no other row came along with it.
  expect(screen.getByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
});

/**
 * Clicking Connect unmounts the Model tab WHILE ITS OWN BUTTON HAS FOCUS, so
 * focus falls to `<body>` inside a focus-trapped panel: the next Tab restarts at
 * the top of the trap, and a screen-reader user hears nothing about the row they
 * were sent to — the reason chip is a `<span>` in a row they never reached.
 */
test("focus lands on the row the founder was sent to", async () => {
  await openWarnedModelTab();
  await screen.findByRole("button", { name: "Cancel Groq" });

  await waitFor(() =>
    expect(document.activeElement).toBe(
      document.querySelector('[data-provider-row="groq"]'),
    ),
  );
});

// The one-shot, through the shell that makes it one: leaving the tab and coming
// back must not re-open a row the founder has already dealt with.
test("returning to Providers later re-opens nothing", async () => {
  await openWarnedModelTab();
  await screen.findByRole("button", { name: "Cancel Groq" });

  await userEvent.click(within(rail()).getByRole("button", { name: /^Model/ }));
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(
    within(rail()).getByRole("button", { name: /^Providers/ }),
  );

  expect(await screen.findByRole("button", { name: "Connect Groq" })).toBeTruthy();
  expect(screen.queryByLabelText("Groq API key")).toBeNull();
  expect(screen.queryByText("Needed by the Fast tier")).toBeNull();
});

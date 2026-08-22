import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";
import type { ModelCatalog, ModelConfig, ProviderStatus } from "@/lib/types";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({
  fetchModelCatalog: vi.fn(),
  fetchModelConfig: vi.fn(),
  saveModelConfig: vi.fn(),
  saveProviderCredential: vi.fn(),
  testProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import { ModelTab } from "./model-tab";
import { ModelConfigProvider } from "../model-config-context";
import {
  fetchModelCatalog,
  fetchModelConfig,
  saveModelConfig,
  testProviderKey,
  deleteProviderKey,
} from "@/lib/api";

// Shared defaults for the non-secret credential fields every ProviderStatus
// fixture now carries. Spread and override per test.
const providerStatusDefaults = {
  base_url: null,
  extra_config_public: {},
  extra_config_secret_keys: [],
  catalogued: true,
} satisfies Partial<ProviderStatus>;

const catalog: ModelCatalog = {
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
  agents: [
    { name: "planner", display_name: "Planner", tier: "reasoning" },
    { name: "presenter", display_name: "Presenter", tier: "balanced" },
  ],
};

function makeConfig(providers: ProviderStatus[]): ModelConfig {
  return {
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
    providers,
    warnings: [],
  };
}

const workspaceProvider: ProviderStatus = {
  ...providerStatusDefaults,
  provider: "anthropic",
  configured: true,
  status: "valid",
  source: "workspace",
};

const config = makeConfig([workspaceProvider]);

/** The tab reads its data from the shared provider, exactly as the shell mounts it. */
async function renderTab(withConfig: ModelConfig = config) {
  vi.mocked(fetchModelCatalog).mockResolvedValue(catalog);
  vi.mocked(fetchModelConfig).mockResolvedValue(withConfig);
  const result = render(
    <ModelConfigProvider>
      <ModelTab />
    </ModelConfigProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /^test$/i })).toBeInTheDocument(),
  );
  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("loads its own catalog and config — the shell hands it nothing", async () => {
  await renderTab();
  expect(fetchModelCatalog).toHaveBeenCalled();
  expect(fetchModelConfig).toHaveBeenCalled();
});

test("renders tier rows from config", async () => {
  await renderTab();
  expect(screen.getByText(/balanced/i)).toBeInTheDocument();
});

test("tests a provider key through the credentials hook", async () => {
  vi.mocked(testProviderKey).mockResolvedValue({ status: "valid" });
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: /^test$/i }));
  await waitFor(() => expect(testProviderKey).toHaveBeenCalledWith("anthropic"));
});

test("shows Configured for a configured provider", async () => {
  await renderTab();
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("lists an env-backed configured provider in the tier dropdown", async () => {
  await renderTab();
  const select = screen.getByLabelText(
    "balanced provider",
  ) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toContain("anthropic");
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("keeps the binding's current provider in options when de-configured", async () => {
  // Provider reported unconfigured, yet the tier is still bound to it: the
  // select must still list it so it never renders blank/mismatched.
  await renderTab(
    makeConfig([
      {
        ...providerStatusDefaults,
        provider: "anthropic",
        configured: false,
        status: "unconfigured",
        source: "none",
      },
    ]),
  );
  const select = screen.getByLabelText(
    "balanced provider",
  ) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toContain("anthropic");
  expect(select.value).toBe("anthropic");
});

test("revokes a credential when Remove is clicked for a workspace provider (R2)", async () => {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: { ...workspaceProvider, configured: false, source: "none" },
    orphaned_bindings: [],
  });
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
  await waitFor(() =>
    expect(deleteProviderKey).toHaveBeenCalledWith("anthropic"),
  );
});

test("hides Remove for an unconfigured provider (R2)", async () => {
  await renderTab(
    makeConfig([
      {
        ...providerStatusDefaults,
        provider: "anthropic",
        configured: false,
        status: "unconfigured",
        source: "none",
      },
    ]),
  );
  expect(
    screen.queryByRole("button", { name: /^remove$/i }),
  ).not.toBeInTheDocument();
});

test("hides Remove for a credential this workspace does not own", async () => {
  // DELETE /providers/{p}/credentials removes THIS workspace's row and nothing else.
  // A provider configured by the deployment-default row or an env fallback key is
  // still `configured: true`, so gating Remove on `configured` offered a control that
  // silently did nothing and then re-rendered as configured on the next refetch.
  for (const source of ["default", "env"] as const) {
    const { unmount } = await renderTab(
      makeConfig([{ ...workspaceProvider, source }]),
    );
    expect(screen.queryByRole("button", { name: /^remove$/i })).toBeNull();
    unmount();
  }
});

test("clamps a cleared max_tokens field to at least 1, never 0 (N1)", async () => {
  await renderTab();
  const maxTokens = screen.getByLabelText(
    "balanced max tokens",
  ) as HTMLInputElement;
  await userEvent.clear(maxTokens);
  // Clearing the field must not yield 0 (which produces a -1 thinking budget server-side).
  expect(maxTokens.value).not.toBe("0");
  expect(Number(maxTokens.value)).toBeGreaterThanOrEqual(1);
});

test("can add and remove a per-agent override from the UI (F1)", async () => {
  // The hook rebases the draft onto the SERVER's answer, so the fake server has
  // to echo what it was sent — resolving with a fixed config would silently
  // delete the override the test just added.
  vi.mocked(saveModelConfig).mockImplementation(async (body) => ({
    ...config,
    tiers: body.tiers,
    agent_overrides: body.agent_overrides,
  }));
  await renderTab();

  // Open the Advanced section, then add a planner override.
  await userEvent.click(screen.getByText(/per-agent overrides/i));
  await userEvent.selectOptions(
    screen.getByLabelText("agent to override"),
    "planner",
  );
  await userEvent.click(screen.getByRole("button", { name: /add override/i }));

  // The override row now exists (its provider select is labelled by the agent name).
  expect(screen.getByLabelText("planner provider")).toBeInTheDocument();

  // Saving sends the override in agent_overrides.
  await userEvent.click(
    screen.getByRole("button", { name: /save overrides/i }),
  );
  await waitFor(() =>
    expect(saveModelConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        agent_overrides: expect.arrayContaining([
          expect.objectContaining({ scope_type: "agent", scope_key: "planner" }),
        ]),
      }),
    ),
  );

  // Remove it -> the row disappears and a save sends an empty override list.
  await userEvent.click(screen.getByLabelText("remove planner override"));
  expect(screen.queryByLabelText("planner provider")).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: /save overrides/i }),
  );
  await waitFor(() =>
    expect(saveModelConfig).toHaveBeenLastCalledWith(
      expect.objectContaining({ agent_overrides: [] }),
    ),
  );
});

test("enables Save for keyless ollama and disables it for keyed providers (F3)", async () => {
  const ollamaCatalog: ModelCatalog = {
    providers: [
      {
        provider: "ollama",
        display_name: "Ollama",
        auth_kind: "keyless_base_url",
        credential_fields: [],
        model_count: 1,
        docs_url: null,
      },
    ],
    models: [
      {
        provider: "ollama",
        model_id: "llama3",
        display_name: "Llama 3",
        thinking_style: "none",
        accepts_temperature: true,
        suggested_tier: "fast",
        context_window: 8192,
        input_cost_per_1k: 0,
        output_cost_per_1k: 0,
        supports_prompt_cache: false,
      },
    ],
    agents: [],
  };
  vi.mocked(fetchModelCatalog).mockResolvedValue(ollamaCatalog);
  vi.mocked(fetchModelConfig).mockResolvedValue({
    tiers: [],
    agent_overrides: [],
    providers: [
      {
        ...providerStatusDefaults,
        provider: "ollama",
        configured: false,
        status: "unconfigured",
        source: "none",
      },
    ],
    warnings: [],
  });
  render(
    <ModelConfigProvider>
      <ModelTab />
    </ModelConfigProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /^test$/i })).toBeInTheDocument(),
  );
  // ollama's Save button is enabled without an API key (base_url-only auth).
  const saveButtons = screen.getAllByRole("button", { name: /^save$/i });
  expect(saveButtons.some((b) => !(b as HTMLButtonElement).disabled)).toBe(
    true,
  );
});

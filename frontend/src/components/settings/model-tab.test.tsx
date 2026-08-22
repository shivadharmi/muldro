import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { ModelTab } from "./model-tab";
import type { ModelCatalog, ModelConfig, ProviderStatus } from "@/lib/types";

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

const config: ModelConfig = {
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
      ...providerStatusDefaults,
      provider: "anthropic",
      configured: true,
      status: "valid",
      source: "workspace",
    },
  ],
  warnings: [],
};

test("renders tier rows from config", async () => {
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
    />,
  );
  await waitFor(() =>
    expect(screen.getByText(/balanced/i)).toBeInTheDocument(),
  );
});

test("calls onLoad on mount", () => {
  const onLoad = vi.fn();
  render(
    <ModelTab
      open
      loading={false}
      catalog={null}
      config={null}
      onLoad={onLoad}
    />,
  );
  expect(onLoad).toHaveBeenCalled();
});

test("fires onTestProvider when Test is clicked", async () => {
  const onTestProvider = vi.fn();
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
      onTestProvider={onTestProvider}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: /test/i }));
  expect(onTestProvider).toHaveBeenCalledWith("anthropic");
});

test("shows Configured for a configured provider", () => {
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
    />,
  );
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("lists an env-backed configured provider in the tier dropdown", () => {
  // anthropic is reported configured (env-backed) with no explicit credential row.
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
    />,
  );
  const select = screen.getByLabelText(
    "balanced provider",
  ) as HTMLSelectElement;
  const options = Array.from(select.options).map((o) => o.value);
  expect(options).toContain("anthropic");
  // And its provider card still surfaces the configured badge.
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("keeps the binding's current provider in options when de-configured", () => {
  // Provider reported unconfigured, yet the tier is still bound to it: the
  // select must still list it so it never renders blank/mismatched.
  const deconfigured: ModelConfig = {
    ...config,
    providers: [
      {
        ...providerStatusDefaults,
        provider: "anthropic",
        configured: false,
        status: "unconfigured",
        source: "none",
      },
    ],
  };
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={deconfigured}
      onLoad={() => {}}
    />,
  );
  const select = screen.getByLabelText(
    "balanced provider",
  ) as HTMLSelectElement;
  const options = Array.from(select.options).map((o) => o.value);
  expect(options).toContain("anthropic");
  expect(select.value).toBe("anthropic");
});

test("fires onDeleteProvider when Remove is clicked for a configured provider (R2)", async () => {
  const onDeleteProvider = vi.fn();
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
      onDeleteProvider={onDeleteProvider}
    />,
  );
  // config has anthropic configured -> the Remove control is shown.
  await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
  expect(onDeleteProvider).toHaveBeenCalledWith("anthropic");
});

test("hides Remove for an unconfigured provider (R2)", () => {
  const deconfigured: ModelConfig = {
    ...config,
    providers: [
      {
        ...providerStatusDefaults,
        provider: "anthropic",
        configured: false,
        status: "unconfigured",
        source: "none",
      },
    ],
  };
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={deconfigured}
      onLoad={() => {}}
      onDeleteProvider={() => {}}
    />,
  );
  expect(
    screen.queryByRole("button", { name: /^remove$/i }),
  ).not.toBeInTheDocument();
});

test("clamps a cleared max_tokens field to at least 1, never 0 (N1)", async () => {
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
    />,
  );
  const maxTokens = screen.getByLabelText(
    "balanced max tokens",
  ) as HTMLInputElement;
  await userEvent.clear(maxTokens);
  // Clearing the field must not yield 0 (which produces a -1 thinking budget server-side).
  expect(maxTokens.value).not.toBe("0");
  expect(Number(maxTokens.value)).toBeGreaterThanOrEqual(1);
});

test("can add and remove a per-agent override from the UI (F1)", async () => {
  const onSaveConfig = vi.fn();
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
      onSaveConfig={onSaveConfig}
    />,
  );

  // Open the Advanced section, then add a planner override.
  await userEvent.click(screen.getByText(/per-agent overrides/i));
  await userEvent.selectOptions(
    screen.getByLabelText("agent to override"),
    "planner",
  );
  await userEvent.click(screen.getByRole("button", { name: /add override/i }));

  // The override row now exists (its provider select is labelled by the agent name),
  // seeded from the reasoning tier's provider.
  const plannerProvider = screen.getByLabelText(
    "planner provider",
  ) as HTMLSelectElement;
  expect(plannerProvider).toBeInTheDocument();

  // Saving sends the override in agent_overrides.
  await userEvent.click(
    screen.getByRole("button", { name: /save overrides/i }),
  );
  expect(onSaveConfig).toHaveBeenCalledWith(
    expect.objectContaining({
      agent_overrides: expect.arrayContaining([
        expect.objectContaining({ scope_type: "agent", scope_key: "planner" }),
      ]),
    }),
  );

  // Remove it -> the row disappears and a save sends an empty override list.
  await userEvent.click(screen.getByLabelText("remove planner override"));
  expect(screen.queryByLabelText("planner provider")).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: /save overrides/i }),
  );
  expect(onSaveConfig).toHaveBeenLastCalledWith(
    expect.objectContaining({ agent_overrides: [] }),
  );
});

test("enables Save for keyless ollama and disables it for keyed providers (F3)", () => {
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
  const ollamaConfig: ModelConfig = {
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
  };
  render(
    <ModelTab
      open
      loading={false}
      catalog={ollamaCatalog}
      config={ollamaConfig}
      onLoad={() => {}}
    />,
  );
  // ollama's Save button is enabled without an API key (base_url-only auth).
  const saveButtons = screen.getAllByRole("button", { name: /^save$/i });
  expect(saveButtons.some((b) => !(b as HTMLButtonElement).disabled)).toBe(
    true,
  );
});

test("hides Remove for a credential this workspace does not own", () => {
  // DELETE /providers/{p}/credentials removes THIS workspace's row and nothing else.
  // A provider configured by the deployment-default row or an env fallback key is
  // still `configured: true`, so gating Remove on `configured` offered a control that
  // silently did nothing and then re-rendered as configured on the next refetch.
  for (const source of ["default", "env"] as const) {
    const inherited: ModelConfig = {
      ...config,
      providers: [
        {
          ...providerStatusDefaults,
          provider: "anthropic",
          configured: true,
          status: "valid",
          source,
        },
      ],
    };
    const { unmount } = render(
      <ModelTab
        open
        loading={false}
        catalog={catalog}
        config={inherited}
        onLoad={() => {}}
        onDeleteProvider={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /^remove$/i })).toBeNull();
    unmount();
  }
});

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

import { ProvidersTab } from "./providers-tab";
import { ModelConfigProvider } from "../model-config-context";
import {
  fetchModelCatalog,
  fetchModelConfig,
  deleteProviderKey,
} from "@/lib/api";

const statusDefaults = {
  base_url: null,
  extra_config_public: {},
  extra_config_secret_keys: [],
  catalogued: true,
} satisfies Partial<ProviderStatus>;

function connected(provider: string): ProviderStatus {
  return {
    ...statusDefaults,
    provider,
    configured: true,
    status: "valid",
    source: "workspace",
  };
}

function notConnected(provider: string, catalogued = true): ProviderStatus {
  return {
    ...statusDefaults,
    provider,
    catalogued,
    configured: false,
    status: "unconfigured",
    source: "none",
  };
}

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
      provider: "openai",
      display_name: "OpenAI",
      auth_kind: "api_key",
      credential_fields: [
        {
          key: "api_key",
          label: "OpenAI API key",
          kind: "secret",
          required: true,
          placeholder: null,
        },
      ],
      model_count: 1,
      docs_url: null,
    },
    {
      provider: "ollama",
      display_name: "Ollama",
      auth_kind: "keyless_base_url",
      credential_fields: [
        {
          key: "base_url",
          label: "Ollama base URL",
          kind: "url",
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
      provider: "anthropic",
      model_id: "claude-sonnet-4-6",
      display_name: "Claude Sonnet 4.6",
      thinking_style: "anthropic_adaptive",
      accepts_temperature: false,
      suggested_tier: "balanced",
      context_window: 200000,
      input_cost_per_1k: 0.003,
      output_cost_per_1k: 0.015,
      supports_prompt_cache: true,
    },
    {
      provider: "openai",
      model_id: "gpt-5",
      display_name: "GPT-5",
      thinking_style: "openai_effort",
      accepts_temperature: false,
      suggested_tier: "reasoning",
      context_window: 400000,
      input_cost_per_1k: 0.005,
      output_cost_per_1k: 0.02,
      supports_prompt_cache: true,
    },
  ],
  agents: [{ name: "planner", display_name: "Planner", tier: "reasoning" }],
};

/** Only the `fast` tier is bound, and it is bound to OpenAI — so OpenAI has a
 *  dependent binding and Anthropic deliberately has none. */
const config: ModelConfig = {
  tiers: [
    {
      scope_type: "tier",
      scope_key: "fast",
      provider: "openai",
      model_id: "gpt-5",
      effort: "low",
      max_tokens: 2000,
      temperature: null,
    },
  ],
  agent_overrides: [],
  providers: [
    connected("anthropic"),
    connected("openai"),
    notConnected("ollama"),
    notConnected("legacy_vendor", false),
  ],
  warnings: [],
};

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModelCatalog).mockResolvedValue(deepClone(catalog));
  vi.mocked(fetchModelConfig).mockResolvedValue(deepClone(config));
});

async function renderTab() {
  render(
    <ModelConfigProvider>
      <ProvidersTab />
    </ModelConfigProvider>,
  );
  // The provider loads on mount; wait for the first row to exist.
  await screen.findByRole("button", { name: "Edit Anthropic" });
}

// A founder names the MODEL, not the vendor that hosts it.
test("searching by a model name matches its provider", async () => {
  await renderTab();
  await userEvent.type(screen.getByRole("searchbox"), "sonnet");

  expect(screen.getByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit OpenAI" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect Ollama" })).toBeNull();
});

test("the search placeholder counts the real provider list", async () => {
  await renderTab();
  expect(screen.getByPlaceholderText("Search 4 providers")).toBeTruthy();
});

test("the segmented filter narrows the groups", async () => {
  await renderTab();
  expect(screen.getByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Available" }));
  expect(screen.queryByRole("heading", { name: "Connected" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Connected" }));
  expect(screen.getByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Available" })).toBeNull();
});

test("group headers carry the count of the rows they hold", async () => {
  await renderTab();
  expect(screen.getByTestId("provider-count-connected").textContent).toBe("2");
  expect(screen.getByTestId("provider-count-available").textContent).toBe("2");
});

// A row cannot enforce exclusivity about its siblings, so the tab owns it.
test("expansion is exclusive — opening a second row closes the first", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Connect Ollama" }));
  expect(screen.getByLabelText("Ollama base URL")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Edit Anthropic" }));
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
  expect(screen.getByLabelText("Anthropic API key")).toBeTruthy();
});

test("Cancel collapses the expanded row", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Connect Ollama" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel Ollama" }));
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
});

// The dependency set is computed from the config ON SCREEN, because
// `orphaned_bindings` only exists once the credential is already gone.
test("Remove asks first when bindings depend on the provider", async () => {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: notConnected("openai"),
    orphaned_bindings: [],
  });
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  expect(deleteProviderKey).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain(
    "Removing OpenAI breaks the fast tier",
  );

  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("openai"));
  expect(screen.queryByRole("alert")).toBeNull();
});

// It must never BLOCK: a credential the founder cannot revoke is a security
// problem, so the confirmation is a sentence and a second click, not a veto.
test("the confirmation can be declined without deleting", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(deleteProviderKey).not.toHaveBeenCalled();
});

test("Remove does not block a provider nothing depends on", async () => {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: notConnected("anthropic"),
    orphaned_bindings: [],
  });
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("anthropic"));
  expect(screen.queryByRole("alert")).toBeNull();
});

// The post-delete truth still gets reported, even though the confirmation was
// answered from the config on screen.
test("orphaned bindings reported by the delete are surfaced", async () => {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: notConnected("anthropic"),
    orphaned_bindings: [
      {
        scope_type: "tier",
        scope_key: "balanced",
        provider: "anthropic",
        code: "provider_not_configured",
        message: "The balanced tier has no configured provider.",
      },
    ],
  });
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining("The balanced tier has no configured provider."),
      "warning",
    ),
  );
});

// No catalog entry means no credential schema, so there is no form to render.
test("an uncatalogued provider renders without a credential form", async () => {
  await renderTab();
  expect(
    screen.getByRole("button", { name: "Remove legacy_vendor" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect legacy_vendor" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Test legacy_vendor" })).toBeNull();
});

test("the subtitle points app connections at /integrations", async () => {
  await renderTab();
  const link = screen.getByRole("link", { name: "Integrations" });
  expect(link.getAttribute("href")).toBe("/integrations");
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

import type {
  CatalogModel,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";

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

import { fetchModelCatalog, fetchModelConfig, saveModelConfig } from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import { ModelConfigProvider } from "../model-config-context";
import { ModelTab } from "./model-tab";

function model(over: Partial<CatalogModel>): CatalogModel {
  return {
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    display_name: "Claude Opus 4.5",
    thinking_style: "anthropic_adaptive",
    accepts_temperature: true,
    suggested_tier: "reasoning",
    context_window: 200000,
    input_cost_per_1k: 0.005,
    output_cost_per_1k: 0.025,
    supports_prompt_cache: true,
    ...over,
  };
}

const catalog: ModelCatalog = {
  providers: [
    {
      provider: "anthropic",
      display_name: "Anthropic",
      auth_kind: "api_key",
      credential_fields: [],
      model_count: 3,
      docs_url: null,
    },
    {
      provider: "groq",
      display_name: "Groq",
      auth_kind: "api_key",
      credential_fields: [],
      model_count: 1,
      docs_url: null,
    },
  ],
  models: [
    model({}),
    model({
      model_id: "claude-sonnet-4-6",
      display_name: "Claude Sonnet 4.6",
      suggested_tier: "balanced",
    }),
    model({
      model_id: "claude-haiku-4-5",
      display_name: "Claude Haiku 4.5",
      suggested_tier: "fast",
    }),
    model({
      provider: "groq",
      model_id: "llama-3.3-70b",
      display_name: "Llama 3.3 70B",
      thinking_style: "none",
      suggested_tier: "fast",
      context_window: 128000,
      input_cost_per_1k: 0.00059,
      output_cost_per_1k: 0.00079,
    }),
  ],
  agents: [
    { name: "planner", display_name: "Planner", tier: "reasoning" },
    { name: "presenter", display_name: "Presenter", tier: "balanced" },
    { name: "persona", display_name: "Persona", tier: "fast" },
  ],
};

function tier(scopeKey: string, modelId: string) {
  return {
    scope_type: "tier" as const,
    scope_key: scopeKey,
    provider: "anthropic",
    model_id: modelId,
    effort: "medium" as const,
    max_tokens: 4096,
    temperature: null,
  };
}

function status(provider: string): ProviderStatus {
  return {
    provider,
    configured: true,
    status: "valid",
    source: "workspace",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
  };
}

const config: ModelConfig = {
  tiers: [
    tier("reasoning", "claude-opus-4-5"),
    tier("balanced", "claude-sonnet-4-6"),
    tier("fast", "claude-haiku-4-5"),
  ],
  agent_overrides: [],
  providers: [status("anthropic"), status("groq")],
  warnings: [],
};

/** The server echoes what it was sent — the hook rebases onto the RESPONSE, so
 *  a fixed reply would silently undo whatever the test just changed. */
function echoServer() {
  vi.mocked(saveModelConfig).mockImplementation(async (body) => ({
    ...config,
    tiers: body.tiers,
    agent_overrides: body.agent_overrides,
  }));
}

async function renderTab(withConfig: ModelConfig = config) {
  vi.mocked(fetchModelCatalog).mockResolvedValue(catalog);
  vi.mocked(fetchModelConfig).mockResolvedValue(withConfig);
  const view = render(
    <ModelConfigProvider>
      <ModelTab />
    </ModelConfigProvider>,
  );
  await waitFor(() => expect(card("Reasoning")).toBeInTheDocument());
  return view;
}

const card = (name: string) => screen.getByRole("region", { name });
const saveBar = () =>
  screen.getByRole("region", { name: /save model configuration/i });
const saveButton = () => screen.getByRole("button", { name: /^save changes$/i });

/** Dirty one tier by nudging its Max tokens — the one control every model
 *  supports, so no test depends on a model's thinking style. */
async function nudge(tierName: string) {
  await userEvent.type(within(card(tierName)).getByLabelText("Max tokens"), "0");
}

async function openOverrides() {
  await userEvent.click(
    screen.getByRole("button", { name: /per-agent overrides/i }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useSettingsModalStore.setState({ activeTab: "model", pendingProvider: null });
});

/** Fast bound to Groq, which resolves no credential — the state §9.6 renders as
 *  a consequence and offers `Connect Groq` for. The binding has to name the
 *  warned provider or `TierCard` ignores the warning as already answered. */
const warnedConfig: ModelConfig = {
  ...config,
  tiers: [
    tier("reasoning", "claude-opus-4-5"),
    tier("balanced", "claude-sonnet-4-6"),
    { ...tier("fast", "llama-3.3-70b"), provider: "groq" },
  ],
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

// The slug alone would land the founder on a list of providers with nothing
// said about which one they came for — the defect this wiring closes.
test("Connect on a warned tier opens Providers FOR that provider", async () => {
  await renderTab(warnedConfig);
  await userEvent.click(screen.getByRole("button", { name: "Connect Groq" }));

  const state = useSettingsModalStore.getState();
  expect(state.activeTab).toBe("providers");
  // The reason names the TIER that sent them, not the provider they can see.
  expect(state.pendingProvider).toEqual({
    provider: "groq",
    reason: "Needed by the Fast tier",
  });
});

// The picker's footer has no provider in mind, so it must not invent one.
test("Browse all providers switches tab and names no provider", async () => {
  await renderTab();
  await userEvent.click(within(card("Fast")).getByLabelText(/^Model/));
  await userEvent.click(
    screen.getByRole("button", { name: /browse all providers/i }),
  );

  const state = useSettingsModalStore.getState();
  expect(state.activeTab).toBe("providers");
  expect(state.pendingProvider).toBeNull();
});

test("F3: Save is inert until something changes, and counts what did", async () => {
  await renderTab();

  expect(within(saveBar()).getByText("No changes")).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
  expect(screen.getByRole("button", { name: /^discard$/i })).toBeDisabled();

  await nudge("Balanced");
  expect(saveButton()).toBeEnabled();
  expect(within(saveBar()).getByText(/1 unsaved change\b/)).toBeInTheDocument();

  await nudge("Fast");
  expect(within(saveBar()).getByText(/2 unsaved changes/)).toBeInTheDocument();
});

test("F2: one save affordance, and it persists tiers AND overrides together", async () => {
  echoServer();
  await renderTab();
  await openOverrides();

  // Both surfaces are on screen — tiers above, overrides expanded below — and
  // between them there is exactly one Save. The deleted second button ("Save
  // overrides") sent this same whole-draft body.
  expect(screen.getAllByRole("button", { name: /^save/i })).toHaveLength(1);

  await userEvent.selectOptions(
    screen.getByLabelText(/add an override/i),
    "presenter",
  );
  await userEvent.click(screen.getByRole("button", { name: /^add override$/i }));
  await nudge("Balanced");

  await userEvent.click(saveButton());

  await waitFor(() => expect(saveModelConfig).toHaveBeenCalledTimes(1));
  const body = vi.mocked(saveModelConfig).mock.calls[0][0];
  expect(body.tiers).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ scope_key: "balanced", max_tokens: 40960 }),
    ]),
  );
  expect(body.agent_overrides).toEqual([
    expect.objectContaining({ scope_type: "agent", scope_key: "presenter" }),
  ]);
});

test("F1: the picker sets provider and model_id together; no provider control exists", async () => {
  echoServer();
  await renderTab();

  // The pair that made F1 possible is gone: nothing on this tab is labelled
  // "provider", so nothing can rewrite one key as a side effect of the other.
  expect(screen.queryByLabelText(/provider/i)).toBeNull();

  await userEvent.click(within(card("Reasoning")).getByLabelText(/^Model/));
  await userEvent.click(screen.getByRole("option", { name: /Llama 3\.3 70B/ }));

  expect(
    within(card("Reasoning")).getByLabelText(/^Model Llama 3\.3 70B/),
  ).toBeInTheDocument();

  await userEvent.click(saveButton());
  await waitFor(() => expect(saveModelConfig).toHaveBeenCalledTimes(1));
  expect(vi.mocked(saveModelConfig).mock.calls[0][0].tiers).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        scope_key: "reasoning",
        provider: "groq",
        model_id: "llama-3.3-70b",
      }),
    ]),
  );
});

test("a 422 lands on the refused card, not in a toast", async () => {
  vi.mocked(saveModelConfig).mockRejectedValue({
    bindRejections: [
      {
        scope_type: "tier",
        scope_key: "fast",
        provider: "groq",
        code: "provider_not_configured",
        message: "Groq is not connected, so Fast was not saved.",
      },
    ],
  });
  await renderTab();
  await nudge("Fast");
  await userEvent.click(saveButton());

  await waitFor(() =>
    expect(
      within(card("Fast")).getByText(/Groq is not connected, so Fast/),
    ).toBeInTheDocument(),
  );
  // The verdict names a binding; a toast could not, so it must not duplicate it.
  expect(addToast).not.toHaveBeenCalled();
  // …and it is on the OFFENDING card only.
  expect(
    within(card("Balanced")).queryByText(/not connected/),
  ).not.toBeInTheDocument();
});

test("the save bar names the changed tiers, not just their number", async () => {
  await renderTab();
  await nudge("Fast");
  await nudge("Reasoning");

  // §9.6 substitutes a warned card's meta row, so the per-card "Changed — not
  // saved" marker cannot be relied on. The names live here instead.
  const bar = within(saveBar()).getByText(/2 unsaved changes/);
  expect(bar).toHaveTextContent("Reasoning");
  expect(bar).toHaveTextContent("Fast");
  expect(bar).not.toHaveTextContent("Balanced");
});

test("Discard restores the saved values and empties the count", async () => {
  await renderTab();
  const maxTokens = within(card("Balanced")).getByLabelText("Max tokens");

  await nudge("Balanced");
  expect(maxTokens).toHaveValue(40960);

  await userEvent.click(screen.getByRole("button", { name: /^discard$/i }));
  expect(maxTokens).toHaveValue(4096);
  expect(within(saveBar()).getByText("No changes")).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
});

test("an override can be added and removed, and the add flow cannot overwrite one", async () => {
  echoServer();
  await renderTab();

  expect(
    screen.getByRole("button", { name: /per-agent overrides\s*0 active/i }),
  ).toBeInTheDocument();
  await openOverrides();

  await userEvent.selectOptions(
    screen.getByLabelText(/add an override/i),
    "planner",
  );
  await userEvent.click(screen.getByRole("button", { name: /^add override$/i }));

  // Seeded from the tier the agent rides on, so the new override starts runnable.
  expect(
    within(card("Planner")).getByLabelText(/^Model Claude Opus 4\.5/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /per-agent overrides\s*1 active/i }),
  ).toBeInTheDocument();

  // `upsertBinding` REPLACES silently, so an agent that already has an override
  // must not be offerable a second time.
  const select = screen.getByLabelText(/add an override/i);
  expect(
    within(select).queryByRole("option", { name: "Planner" }),
  ).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
  expect(screen.queryByRole("region", { name: "Planner" })).toBeNull();
  expect(
    within(select).getByRole("option", { name: "Planner" }),
  ).toBeInTheDocument();
});

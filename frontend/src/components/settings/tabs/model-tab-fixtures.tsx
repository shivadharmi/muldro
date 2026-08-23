/**
 * Shared fixtures for the Model tab's test files.
 *
 * The tab's specs split along a real seam: `model-tab.test.tsx` covers what the
 * tab WIRES (the picker's two keys, the 422's destination, cross-tab intent,
 * overrides), and `model-tab-save.test.tsx` covers what the save bar SAYS (the
 * count, the names, discard). One fixture graph serves both, so the two can
 * never quietly diverge into testing two different catalogs.
 *
 * Every export is a FACTORY returning a fresh graph, matching
 * `providers-tab-fixtures.ts`. A shared const would be true-by-convention only:
 * the first test that pushed onto `config().tiers` would leak into every other
 * file importing it, and nothing would say so.
 *
 * Each importing spec still declares its own `vi.mock("@/lib/api", …)` — mocks
 * are hoisted per file and cannot live here — which is what makes the
 * `vi.mocked(…)` calls below resolve to that file's mock.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, vi } from "vitest";

import { fetchModelCatalog, fetchModelConfig, saveModelConfig } from "@/lib/api";
import type {
  CatalogModel,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";
import { ModelConfigProvider } from "../model-config-context";
import { ModelTab } from "./model-tab";

export function model(over: Partial<CatalogModel> = {}): CatalogModel {
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

export function catalog(): ModelCatalog {
  return {
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
      model(),
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
}

export function tier(scopeKey: string, modelId: string): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: scopeKey,
    provider: "anthropic",
    model_id: modelId,
    effort: "medium",
    max_tokens: 4096,
    temperature: null,
  };
}

/** A saved per-agent override. Seeded like the Reasoning tier, so a test can
 *  edit or remove it without first having to add one through the UI. */
export function override(scopeKey: string): ModelBinding {
  return { ...tier(scopeKey, "claude-opus-4-5"), scope_type: "agent", scope_key: scopeKey };
}

export function status(provider: string): ProviderStatus {
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

export function config(over: Partial<ModelConfig> = {}): ModelConfig {
  return {
    tiers: [
      tier("reasoning", "claude-opus-4-5"),
      tier("balanced", "claude-sonnet-4-6"),
      tier("fast", "claude-haiku-4-5"),
    ],
    agent_overrides: [],
    providers: [status("anthropic"), status("groq")],
    warnings: [],
    ...over,
  };
}

/** The server echoes what it was sent — the hook rebases onto the RESPONSE, so
 *  a fixed reply would silently undo whatever the test just changed. */
export function echoServer(): void {
  vi.mocked(saveModelConfig).mockImplementation(async (body) => ({
    ...config(),
    tiers: body.tiers,
    agent_overrides: body.agent_overrides,
  }));
}

export function stubServer(withConfig: ModelConfig = config()): void {
  vi.mocked(fetchModelCatalog).mockResolvedValue(catalog());
  vi.mocked(fetchModelConfig).mockResolvedValue(withConfig);
}

/**
 * Mount WITHOUT stubbing the fetches and without waiting for a card.
 *
 * Separate from {@link renderTab} because a test of the FAILED load has to
 * arrange its own rejection: a helper that stubs on the way in would overwrite
 * it, and the tab would render the happy path while the test read as if it did
 * not.
 */
export function mountTab() {
  return render(
    <ModelConfigProvider>
      <ModelTab />
    </ModelConfigProvider>,
  );
}

export async function renderTab(withConfig: ModelConfig = config()) {
  stubServer(withConfig);
  const view = mountTab();
  await waitFor(() => expect(card("Reasoning")).toBeInTheDocument());
  return view;
}

export const card = (name: string) => screen.getByRole("region", { name });
export const saveBar = () =>
  screen.getByRole("region", { name: /save model configuration/i });
export const saveButton = () =>
  screen.getByRole("button", { name: /^save changes$/i });
export const discardButton = () =>
  screen.getByRole("button", { name: /^discard$/i });

/** Dirty one tier by nudging its Max tokens — the one control every model
 *  supports, so no test depends on a model's thinking style. */
export async function nudge(tierName: string) {
  await userEvent.type(within(card(tierName)).getByLabelText("Max tokens"), "0");
}

export async function openOverrides() {
  await userEvent.click(
    screen.getByRole("button", { name: /per-agent overrides/i }),
  );
}

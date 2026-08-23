import { test, expect } from "vitest";

import type {
  ModelBinding,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";
import {
  buildEntries,
  dependentBindings,
  filterEntries,
} from "./provider-entries";

function status(provider: string, over: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider,
    configured: true,
    status: "valid",
    source: "workspace",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
    ...over,
  };
}

function binding(over: Partial<ModelBinding> = {}): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: "fast",
    provider: "openai",
    model_id: "gpt-5",
    effort: "low",
    max_tokens: 2000,
    temperature: null,
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
      model_count: 1,
      docs_url: null,
    },
    {
      provider: "openai",
      display_name: "OpenAI",
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
  agents: [],
};

const config: ModelConfig = {
  tiers: [binding(), binding({ scope_key: "balanced", provider: "anthropic" })],
  agent_overrides: [
    binding({ scope_type: "agent", scope_key: "planner", provider: "openai" }),
  ],
  providers: [
    status("anthropic"),
    status("openai"),
    status("legacy_vendor", { catalogued: false, configured: false, source: "none" }),
  ],
  warnings: [],
};

const entries = () => buildEntries(config, catalog);

test("an uncatalogued provider gets no catalog entry even when the catalog lists it", () => {
  const stale: ModelConfig = {
    ...config,
    providers: [status("anthropic", { catalogued: false })],
  };
  expect(buildEntries(stale, catalog)[0].entry).toBeNull();
});

test("the haystack carries slug, display name, model names and model ids", () => {
  const anthropic = entries()[0];
  for (const term of ["anthropic", "claude sonnet 4.6", "claude-sonnet-4-6"]) {
    expect(anthropic.haystack).toContain(term);
  }
});

test("a model name matches its provider", () => {
  expect(filterEntries(entries(), "sonnet").map((e) => e.status.provider)).toEqual([
    "anthropic",
  ]);
});

// The haystack concatenates four fields, so the joins between them are not word
// boundaries a founder can see — a single-substring match would return nothing.
test("every term must match, so words from different fields can be combined", () => {
  const found = filterEntries(entries(), "anthropic sonnet");
  expect(found.map((e) => e.status.provider)).toEqual(["anthropic"]);
});

test("term matching is case- and whitespace-insensitive", () => {
  const found = filterEntries(entries(), "  OpenAI   GPT-5 ");
  expect(found.map((e) => e.status.provider)).toEqual(["openai"]);
});

test("a term that matches nothing narrows to nothing", () => {
  expect(filterEntries(entries(), "anthropic gpt-5")).toEqual([]);
});

test("a blank query returns every entry", () => {
  expect(filterEntries(entries(), "   ")).toHaveLength(3);
});

test("an uncatalogued provider is still findable by its slug", () => {
  expect(filterEntries(entries(), "legacy").map((e) => e.status.provider)).toEqual([
    "legacy_vendor",
  ]);
});

test("dependent bindings span tiers and agent overrides", () => {
  expect(
    dependentBindings(config, "openai").map((b) => b.scope_key),
  ).toEqual(["fast", "planner"]);
});

test("a provider nothing is bound to has no dependents", () => {
  expect(dependentBindings(config, "legacy_vendor")).toEqual([]);
});

test("an unloaded config has no dependents rather than throwing", () => {
  expect(dependentBindings(null, "openai")).toEqual([]);
});

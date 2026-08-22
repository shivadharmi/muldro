import type { CatalogModel, CatalogProvider, ProviderStatus } from "@/lib/types";

/**
 * One catalog, shared by every model-picker test file.
 *
 * Six catalogued providers, three connected and THREE not: the unconnected set
 * is what the footer has to NAME, and a picker that filtered them out of
 * existence is the state that keeps a missing prerequisite invisible (§4.7).
 *
 * Three, not two, so the footer's "+N more" overflow is exercised — at exactly
 * two it was dead code that no test could reach.
 */

function provider(slug: string, displayName: string): CatalogProvider {
  return {
    provider: slug,
    display_name: displayName,
    auth_kind: "api_key",
    credential_fields: [],
    model_count: 2,
    docs_url: null,
  };
}

function status(slug: string, configured: boolean): ProviderStatus {
  return {
    provider: slug,
    configured,
    status: configured ? "ok" : "not_configured",
    source: configured ? "workspace" : "none",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
  };
}

function model(over: Partial<CatalogModel> & Pick<CatalogModel, "model_id">): CatalogModel {
  return {
    provider: "anthropic",
    display_name: "Claude Opus 4.5",
    thinking_style: "anthropic_adaptive",
    accepts_temperature: false,
    suggested_tier: "reasoning",
    context_window: 200000,
    input_cost_per_1k: 0.005,
    output_cost_per_1k: 0.025,
    supports_prompt_cache: true,
    ...over,
  };
}

export const PROVIDERS: CatalogProvider[] = [
  provider("anthropic", "Anthropic"),
  provider("openai", "OpenAI"),
  provider("google_genai", "Google"),
  provider("mistral", "Mistral"),
  provider("cohere", "Cohere"),
  provider("groq", "Groq"),
];

export const STATUSES: ProviderStatus[] = [
  status("anthropic", true),
  status("openai", true),
  status("google_genai", true),
  status("mistral", false),
  status("cohere", false),
  status("groq", false),
];

export const ANTHROPIC_OPUS = model({ model_id: "claude-opus-4-5" });

export const ANTHROPIC_SONNET = model({
  model_id: "claude-sonnet-4-6",
  display_name: "Claude Sonnet 4.6",
  suggested_tier: "balanced",
});

export const OPENAI_GPT = model({
  provider: "openai",
  model_id: "gpt-5.2",
  display_name: "GPT-5.2",
  thinking_style: "openai_effort",
  context_window: 400000,
  input_cost_per_1k: 0.00125,
  output_cost_per_1k: 0.01,
});

export const GEMINI_PRO = model({
  provider: "google_genai",
  model_id: "gemini-3-pro",
  display_name: "Gemini 3 Pro",
  thinking_style: "gemini",
  context_window: 1000000,
});

export const MODELS: CatalogModel[] = [
  ANTHROPIC_OPUS,
  ANTHROPIC_SONNET,
  OPENAI_GPT,
  GEMINI_PRO,
];

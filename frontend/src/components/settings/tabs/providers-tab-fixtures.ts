import type {
  CatalogModel,
  CatalogProvider,
  CredentialFieldSpec,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";

/** Shared fixtures for the Providers tab's two test files.
 *
 *  Every export is a FACTORY returning a fresh graph. A shared const would be
 *  true-by-convention only: the first test that pushes onto `config.tiers` would
 *  leak into every other file importing it, and nothing would say so. */

const statusDefaults = {
  base_url: null,
  extra_config_public: {},
  extra_config_secret_keys: [],
  catalogued: true,
} satisfies Partial<ProviderStatus>;

export function connected(provider: string): ProviderStatus {
  return {
    ...statusDefaults,
    provider,
    configured: true,
    status: "valid",
    source: "workspace",
  };
}

export function notConnected(provider: string, catalogued = true): ProviderStatus {
  return {
    ...statusDefaults,
    provider,
    catalogued,
    configured: false,
    status: "unconfigured",
    source: "none",
  };
}

function field(
  key: string,
  label: string,
  kind: CredentialFieldSpec["kind"],
): CredentialFieldSpec {
  return { key, label, kind, required: true, placeholder: null };
}

function catalogProvider(
  provider: string,
  display_name: string,
  auth_kind: CatalogProvider["auth_kind"],
  credential_fields: CredentialFieldSpec[],
): CatalogProvider {
  return {
    provider,
    display_name,
    auth_kind,
    credential_fields,
    model_count: 1,
    docs_url: null,
  };
}

function catalogModel(
  provider: string,
  model_id: string,
  display_name: string,
): CatalogModel {
  return {
    provider,
    model_id,
    display_name,
    thinking_style: "none",
    accepts_temperature: false,
    suggested_tier: "balanced",
    context_window: 200000,
    input_cost_per_1k: 0.003,
    output_cost_per_1k: 0.015,
    supports_prompt_cache: true,
  };
}

export function makeCatalog(): ModelCatalog {
  return {
    providers: [
      catalogProvider("anthropic", "Anthropic", "api_key", [
        field("api_key", "Anthropic API key", "secret"),
      ]),
      catalogProvider("openai", "OpenAI", "api_key", [
        field("api_key", "OpenAI API key", "secret"),
      ]),
      catalogProvider("ollama", "Ollama", "keyless_base_url", [
        field("base_url", "Ollama base URL", "url"),
      ]),
    ],
    models: [
      catalogModel("anthropic", "claude-sonnet-4-6", "Claude Sonnet 4.6"),
      catalogModel("openai", "gpt-5", "GPT-5"),
    ],
    agents: [{ name: "planner", display_name: "Planner", tier: "reasoning" }],
  };
}

/** Only the `fast` tier is bound, and it is bound to OpenAI — so OpenAI has a
 *  dependent binding and Anthropic deliberately has none. */
export function makeConfig(): ModelConfig {
  return {
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
}

/** The row wrapper focus returns to, by provider slug. */
export function rowAnchor(provider: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(
    `[data-provider-row="${provider}"]`,
  );
  if (!el) throw new Error(`no row for ${provider}`);
  return el;
}

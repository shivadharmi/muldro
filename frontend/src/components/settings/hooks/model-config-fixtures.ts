import type { ModelBinding, ModelCatalog, ModelConfig } from "@/lib/types";

/** Shared fixtures for the model-config hook tests. Every factory returns a
 *  FRESH object graph, so one test mutating a fixture cannot reach another. */

export const CATALOG: ModelCatalog = { providers: [], models: [], agents: [] };

export function binding(
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
  overrides: Partial<ModelBinding> = {},
): ModelBinding {
  const base: ModelBinding = {
    scope_type: scopeType,
    scope_key: scopeKey,
    provider: "anthropic",
    model_id: "claude-sonnet",
    effort: "medium",
    max_tokens: 4000,
    temperature: null,
  };
  // Identity re-asserted last: an `overrides` typo cannot re-key the fixture.
  return { ...base, ...overrides, scope_type: scopeType, scope_key: scopeKey };
}

export function makeConfig(): ModelConfig {
  return {
    tiers: [
      binding("tier", "reasoning", {
        model_id: "claude-opus",
        effort: "high",
        max_tokens: 8000,
      }),
      binding("tier", "fast", {
        model_id: "claude-haiku",
        effort: "low",
        max_tokens: 2000,
      }),
    ],
    agent_overrides: [
      binding("agent", "planner", {
        provider: "openai",
        model_id: "gpt-5",
        temperature: 0.2,
      }),
    ],
    providers: [],
    warnings: [],
  };
}

/**
 * An `ApiError`-shaped 422. The hook detects `bindRejections` structurally, so
 * the real class — which lives in `@/lib/api`, mocked in these tests — is not
 * needed, and the entries mirror what `parseBindRejection` already normalised.
 */
export function bindRejectionError(
  scopeKey: string,
  scopeType: ModelBinding["scope_type"] = "tier",
) {
  return Object.assign(new Error("API 422: no key"), {
    safeMessage: "no key",
    code: "error",
    correlationId: null,
    bindRejections: [
      {
        scope_type: scopeType,
        scope_key: scopeKey,
        provider: "openai",
        code: "provider_not_configured" as const,
        message: "OpenAI is not configured.",
      },
    ],
  });
}

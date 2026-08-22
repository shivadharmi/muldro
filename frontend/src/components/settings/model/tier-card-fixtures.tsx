/**
 * Shared fixtures for the tier-card specs.
 *
 * Extracted because the card's two concerns — the healthy card (§9.5) and the
 * notice states (§9.6, §4.4) — are separate spec files, and duplicating a
 * catalog fixture across them is how two specs quietly start testing two
 * different models. Mirrors `tabs/providers-tab-fixtures.ts`.
 */

import { render, screen } from "@testing-library/react";
import { expect, vi } from "vitest";

import type {
  AgentInfo,
  CatalogModel,
  CatalogProvider,
  ConfigWarning,
  ModelBinding,
} from "@/lib/types";
import { TierCard } from "./tier-card";

/** An adaptive-thinking model that does NOT accept temperature — the default,
 *  because the capability hint is the meta row's more interesting branch. */
export function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    provider: "anthropic",
    model_id: "claude-opus-4-5",
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

/** Sub-cent per-1k pricing, so the ×1000 to Mtok is actually exercised. */
export const GROQ_MODEL = model({
  provider: "groq",
  model_id: "llama-3.3-70b",
  display_name: "Llama 3.3 70B",
  thinking_style: "none",
  accepts_temperature: true,
  suggested_tier: "fast",
  context_window: 128000,
  input_cost_per_1k: 0.00059,
  output_cost_per_1k: 0.00079,
});

export const ANTHROPIC: CatalogProvider = {
  provider: "anthropic",
  display_name: "Anthropic",
  auth_kind: "api_key",
  credential_fields: [],
  model_count: 4,
  docs_url: null,
};

export const GROQ: CatalogProvider = {
  ...ANTHROPIC,
  provider: "groq",
  display_name: "Groq",
};

export const AGENTS: AgentInfo[] = [
  { name: "planner", display_name: "Planner", tier: "reasoning" },
  { name: "perceiver", display_name: "Perceiver", tier: "balanced" },
  { name: "librarian", display_name: "Librarian", tier: "balanced" },
  { name: "persona", display_name: "Persona", tier: "fast" },
];

export function binding(over: Partial<ModelBinding> = {}): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: "reasoning",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    effort: "high",
    max_tokens: 8192,
    temperature: null,
    ...over,
  };
}

/** The Fast tier bound to Groq — the tier the notice fixtures name. */
export const FAST_BINDING: Partial<ModelBinding> = {
  scope_key: "fast",
  provider: "groq",
  model_id: "llama-3.3-70b",
};

export function warningFor(over: Partial<ConfigWarning> = {}): ConfigWarning {
  return {
    scope_type: "tier",
    scope_key: "fast",
    provider: "groq",
    code: "provider_not_configured",
    message:
      "Groq is not connected. There is no tier fallback — every agent on Fast " +
      "will fail until you connect it.",
    ...over,
  };
}

export interface CardOverrides {
  binding?: Partial<ModelBinding>;
  models?: CatalogModel[];
  providers?: CatalogProvider[];
  agents?: AgentInfo[];
  description?: string;
  dirty?: boolean;
  disabled?: boolean;
  warning?: ConfigWarning;
  rejection?: ConfigWarning;
}

export function cardProps(over: CardOverrides = {}) {
  return {
    binding: binding(over.binding),
    models: over.models ?? [model(), GROQ_MODEL],
    providers: over.providers ?? [ANTHROPIC, GROQ],
    agents: over.agents ?? AGENTS,
    description: over.description ?? "Deepest reasoning. Slowest, dearest.",
    dirty: over.dirty,
    disabled: over.disabled,
    warning: over.warning,
    rejection: over.rejection,
  };
}

export function renderCard(over: CardOverrides = {}) {
  const props = cardProps(over);
  const onChange = vi.fn();
  const onOpenPicker = vi.fn();
  const onConnectProvider = vi.fn();
  const view = render(
    <TierCard
      {...props}
      onChange={onChange}
      onOpenPicker={onOpenPicker}
      onConnectProvider={onConnectProvider}
    />,
  );
  return {
    ...view,
    value: props.binding,
    onChange,
    onOpenPicker,
    onConnectProvider,
  };
}

/** The card element itself — the only thing carrying §9.6's border. */
export function card(): HTMLElement {
  const region = document.querySelector("section");
  expect(region).not.toBeNull();
  return region as HTMLElement;
}

/** The rendered consequence, whichever notice produced it. */
export function consequenceText(): string {
  return screen.getByText(/is not connected/).textContent ?? "";
}

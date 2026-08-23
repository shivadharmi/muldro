import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import type {
  AgentInfo,
  CatalogModel,
  CatalogProvider,
  ConfigWarning,
  ModelBinding,
} from "@/lib/types";
import { AgentOverrides, seedOverride } from "./agent-overrides";

const OPUS: CatalogModel = {
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
};

const LLAMA: CatalogModel = {
  ...OPUS,
  provider: "groq",
  model_id: "llama-3.3-70b",
  display_name: "Llama 3.3 70B",
  thinking_style: "none",
  accepts_temperature: true,
  suggested_tier: "fast",
  context_window: 128000,
};

const PROVIDERS: CatalogProvider[] = [
  {
    provider: "anthropic",
    display_name: "Anthropic",
    auth_kind: "api_key",
    credential_fields: [],
    model_count: 1,
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
];

const AGENTS: AgentInfo[] = [
  { name: "planner", display_name: "Planner", tier: "reasoning" },
  { name: "persona", display_name: "Persona", tier: "fast" },
];

const TIERS: ModelBinding[] = [
  {
    scope_type: "tier",
    scope_key: "reasoning",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    effort: "high",
    max_tokens: 8192,
    temperature: null,
  },
  {
    scope_type: "tier",
    scope_key: "fast",
    provider: "groq",
    model_id: "llama-3.3-70b",
    effort: "none",
    max_tokens: 2048,
    temperature: 0.4,
  },
];

function override(over: Partial<ModelBinding> = {}): ModelBinding {
  return {
    scope_type: "agent",
    scope_key: "planner",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    effort: "high",
    max_tokens: 8192,
    temperature: null,
    ...over,
  };
}

interface Overrides {
  overrides?: ModelBinding[];
  agents?: AgentInfo[];
  dirty?: (scopeKey: string) => boolean;
  rejection?: (scopeKey: string) => ConfigWarning | undefined;
  disabled?: boolean;
}

function renderSection(over: Overrides = {}) {
  const onChange = vi.fn();
  const onAdd = vi.fn();
  const onRemove = vi.fn();
  const onOpenPicker = vi.fn();
  const view = render(
    <AgentOverrides
      overrides={over.overrides ?? []}
      agents={over.agents ?? AGENTS}
      tiers={TIERS}
      models={[OPUS, LLAMA]}
      providers={PROVIDERS}
      disabled={over.disabled}
      dirty={over.dirty ?? (() => false)}
      rejection={over.rejection ?? (() => undefined)}
      onChange={onChange}
      onAdd={onAdd}
      onRemove={onRemove}
      onOpenPicker={onOpenPicker}
    />,
  );
  return { ...view, onChange, onAdd, onRemove, onOpenPicker };
}

const disclosure = () =>
  screen.getByRole("button", { name: /per-agent overrides/i });

async function expand() {
  await userEvent.click(disclosure());
}

test("starts collapsed and states the active count without being opened", () => {
  renderSection({ overrides: [override(), override({ scope_key: "persona" })] });

  expect(disclosure()).toHaveAttribute("aria-expanded", "false");
  expect(disclosure()).toHaveTextContent("2 active");
  // Collapsing is only safe BECAUSE the count is visible; nothing else is.
  expect(screen.queryByRole("region", { name: "Planner" })).toBeNull();
});

test("expands to one binding grid per override, named by the agent", async () => {
  renderSection({ overrides: [override()] });
  await expand();

  expect(disclosure()).toHaveAttribute("aria-expanded", "true");
  const row = screen.getByRole("region", { name: "Planner" });
  expect(within(row).getByLabelText(/^Model Claude Opus 4\.5/)).toBeInTheDocument();
  expect(within(row).getByLabelText("Max tokens")).toHaveValue(8192);
});

test("an added override is seeded from the tier its agent rides on", async () => {
  const { onAdd } = renderSection();
  await expand();

  await userEvent.selectOptions(
    screen.getByLabelText(/add an override/i),
    "persona",
  );
  await userEvent.click(screen.getByRole("button", { name: /^add override$/i }));

  // Persona rides the `fast` tier, so the seed is that binding re-scoped —
  // never a blank row the founder has to re-choose a working model for.
  expect(onAdd).toHaveBeenCalledWith({
    scope_type: "agent",
    scope_key: "persona",
    provider: "groq",
    model_id: "llama-3.3-70b",
    effort: "none",
    max_tokens: 2048,
    temperature: 0.4,
  });
});

test("Add is inert until an agent is chosen", async () => {
  renderSection();
  await expand();
  expect(screen.getByRole("button", { name: /^add override$/i })).toBeDisabled();
});

test("an agent that already has an override is not offerable a second time", async () => {
  // `upsertBinding` REPLACES silently, so the only safe add flow is one that
  // cannot select a duplicate at all.
  renderSection({ overrides: [override()] });
  await expand();

  const select = screen.getByLabelText(/add an override/i);
  expect(within(select).queryByRole("option", { name: "Planner" })).toBeNull();
  expect(within(select).getByRole("option", { name: "Persona" })).toBeInTheDocument();
});

test("the selector says so, and disables itself, when every agent is overridden", async () => {
  renderSection({
    agents: [{ name: "planner", display_name: "Planner", tier: "reasoning" }],
    overrides: [override()],
  });
  await expand();

  const select = screen.getByLabelText(/add an override/i);
  expect(select).toBeDisabled();
  expect(select).toHaveTextContent(/every agent is overridden/i);
});

test("edits leave as patches; the picker and removal leave as scope keys", async () => {
  const { onChange, onRemove, onOpenPicker } = renderSection({
    overrides: [override()],
  });
  await expand();
  const row = screen.getByRole("region", { name: "Planner" });

  await userEvent.type(within(row).getByLabelText("Max tokens"), "0");
  expect(onChange).toHaveBeenLastCalledWith("planner", { max_tokens: 81920 });

  await userEvent.click(within(row).getByLabelText(/^Model/));
  expect(onOpenPicker).toHaveBeenCalledWith("planner");

  await userEvent.click(within(row).getByRole("button", { name: /^remove$/i }));
  expect(onRemove).toHaveBeenCalledWith("planner");
});

test("a 422 renders on the refused override, never as a toast this cannot raise", async () => {
  const rejection = vi.fn((scopeKey: string) =>
    scopeKey === "planner"
      ? {
          scope_type: "agent" as const,
          scope_key: "planner",
          provider: "anthropic",
          code: "provider_not_configured" as const,
          message: "Anthropic is not connected, so Planner was not saved.",
        }
      : undefined,
  );
  renderSection({ overrides: [override(), override({ scope_key: "persona" })], rejection });
  await expand();

  expect(
    within(screen.getByRole("region", { name: "Planner" })).getByRole("alert"),
  ).toHaveTextContent(/Anthropic is not connected/);
  expect(
    within(screen.getByRole("region", { name: "Persona" })).queryByRole("alert"),
  ).toBeNull();
});

test("carries no Save of its own — the tab owns the one save affordance (F2)", async () => {
  renderSection({ overrides: [override()] });
  await expand();
  expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
});

test("disabled locks every control the section owns", async () => {
  renderSection({ overrides: [override()], disabled: true });
  await expand();
  const row = screen.getByRole("region", { name: "Planner" });

  expect(within(row).getByRole("button", { name: /^remove$/i })).toBeDisabled();
  expect(within(row).getByLabelText("Max tokens")).toBeDisabled();
  expect(screen.getByLabelText(/add an override/i)).toBeDisabled();
});

test("seedOverride falls back to a blank binding when the agent's tier is missing", () => {
  const seed = seedOverride(
    { name: "ghost", display_name: "Ghost", tier: "nonexistent" },
    TIERS,
  );
  // No provider guess: an empty model_id renders "Select a model…" and sends
  // the founder to the picker, the only control that sets both keys together.
  expect(seed).toEqual({
    scope_type: "agent",
    scope_key: "ghost",
    provider: "",
    model_id: "",
    effort: "none",
    max_tokens: 4096,
    temperature: null,
  });
});

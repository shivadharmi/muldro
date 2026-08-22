import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { ModelPicker } from "./model-picker";
import type { CatalogModel, CatalogProvider, ProviderStatus } from "@/lib/types";

function provider(
  slug: string,
  displayName: string,
  over: Partial<CatalogProvider> = {},
): CatalogProvider {
  return {
    provider: slug,
    display_name: displayName,
    auth_kind: "api_key",
    credential_fields: [],
    model_count: 2,
    docs_url: null,
    ...over,
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

// Five catalogued providers; three connected, two not. The two unconnected are
// what the footer has to count — a picker that filtered them out of existence
// is exactly the state that makes a missing prerequisite invisible.
const PROVIDERS: CatalogProvider[] = [
  provider("anthropic", "Anthropic"),
  provider("openai", "OpenAI"),
  provider("google_genai", "Google"),
  provider("mistral", "Mistral"),
  provider("cohere", "Cohere"),
];

const STATUSES: ProviderStatus[] = [
  status("anthropic", true),
  status("openai", true),
  status("google_genai", true),
  status("mistral", false),
  status("cohere", false),
];

const OPUS = model({ model_id: "claude-opus-4-5" });
const SONNET = model({
  model_id: "claude-sonnet-4-6",
  display_name: "Claude Sonnet 4.6",
  suggested_tier: "balanced",
});
const GPT = model({
  provider: "openai",
  model_id: "gpt-5.2",
  display_name: "GPT-5.2",
  thinking_style: "openai_effort",
  context_window: 400000,
  input_cost_per_1k: 0.00125,
  output_cost_per_1k: 0.01,
});
const GEMINI = model({
  provider: "google_genai",
  model_id: "gemini-3-pro",
  display_name: "Gemini 3 Pro",
  thinking_style: "gemini",
  context_window: 1000000,
});

const MODELS: CatalogModel[] = [OPUS, SONNET, GPT, GEMINI];

/**
 * Rendered rows, in the order the keyboard walks them.
 *
 * For `tier="reasoning"` that is: Suggested (Opus, GPT, Gemini), then Anthropic
 * (Opus, Sonnet), OpenAI (GPT), Google (Gemini) — a suggested model appears
 * twice by design, which is why row ids are group-scoped.
 */
function optionTexts(): string[] {
  return screen.getAllByRole("option").map((el) => el.textContent ?? "");
}

function renderPicker(
  over: {
    tier?: string;
    selectedProvider?: string | null;
    selectedModelId?: string | null;
    models?: CatalogModel[];
  } = {},
) {
  const onSelect = vi.fn();
  const onClose = vi.fn();
  const onBrowseProviders = vi.fn();
  render(
    <ModelPicker
      open
      tier={over.tier ?? "reasoning"}
      selectedProvider={
        over.selectedProvider === undefined ? "anthropic" : over.selectedProvider
      }
      selectedModelId={
        over.selectedModelId === undefined ? "claude-opus-4-5" : over.selectedModelId
      }
      models={over.models ?? MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={onSelect}
      onClose={onClose}
      onBrowseProviders={onBrowseProviders}
    />,
  );
  return { onSelect, onClose, onBrowseProviders, user: userEvent.setup() };
}

// ── Search ─────────────────────────────────────────────────────────────────

test("a term matching another provider's model finds it across group boundaries", async () => {
  const { user } = renderPicker();
  expect(optionTexts().some((t) => t.includes("Claude Opus 4.5"))).toBe(true);

  await user.keyboard("gpt");

  const texts = optionTexts();
  expect(texts.every((t) => t.includes("GPT-5.2"))).toBe(true);
  expect(texts.some((t) => t.includes("Claude"))).toBe(false);
});

test("a multi-word query ANDs its terms rather than matching one string", async () => {
  const { user } = renderPicker();

  // "anthropic" only ever matches the provider column and "sonnet" only the
  // name — never adjacent, so a concatenated `includes` finds nothing here.
  await user.keyboard("anthropic sonnet");

  const texts = optionTexts();
  expect(texts.length).toBeGreaterThan(0);
  expect(texts.every((t) => t.includes("Claude Sonnet 4.6"))).toBe(true);

  await user.clear(screen.getByRole("combobox"));
  await user.keyboard("anthropic gpt");
  expect(screen.queryAllByRole("option")).toHaveLength(0);
});

test("numeric facts are searchable — context window and price", async () => {
  const { user } = renderPicker();

  await user.keyboard("1000000");
  expect(optionTexts().every((t) => t.includes("Gemini 3 Pro"))).toBe(true);

  await user.clear(screen.getByRole("combobox"));
  await user.keyboard("$1.25");
  expect(optionTexts().every((t) => t.includes("GPT-5.2"))).toBe(true);
});

// ── Footer: unconnected providers are surfaced, not filtered away ───────────

test("the footer counts the providers that are NOT connected and can route to them", async () => {
  const { onBrowseProviders, user } = renderPicker();

  expect(screen.getByText(/2 providers not connected/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Browse all providers" }));
  expect(onBrowseProviders).toHaveBeenCalledTimes(1);
});

// ── Keyboard ───────────────────────────────────────────────────────────────

test("arrow keys move the active row and Enter selects that model", async () => {
  const { onSelect, onClose, user } = renderPicker();

  const input = screen.getByRole("combobox");
  const rows = screen.getAllByRole("option");
  expect(input).toHaveAttribute("aria-activedescendant", rows[0].id);

  await user.keyboard("{ArrowDown}");
  expect(input).toHaveAttribute("aria-activedescendant", rows[1].id);

  await user.keyboard("{ArrowUp}{ArrowDown}{ArrowDown}");
  expect(input).toHaveAttribute("aria-activedescendant", rows[2].id);

  await user.keyboard("{Enter}");
  // Suggested group order is the catalog's: Opus, GPT, Gemini.
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledWith(GEMINI);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("Escape closes without touching the binding, and marks the event handled", async () => {
  const { onSelect, onClose, user } = renderPicker();

  // The settings shell closes the WHOLE dialog on an Escape it sees unhandled,
  // so this listener stands in for it: it must observe `defaultPrevented`.
  const seen: boolean[] = [];
  const spy = (e: KeyboardEvent) => {
    if (e.key === "Escape") seen.push(e.defaultPrevented);
  };
  document.addEventListener("keydown", spy);
  try {
    await user.keyboard("{Escape}");
  } finally {
    document.removeEventListener("keydown", spy);
  }

  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSelect).not.toHaveBeenCalled();
  expect(seen).toEqual([true]);
});

// ── A3: display names only, never a raw slug ───────────────────────────────

test("no raw provider or thinking-style slug reaches the screen", () => {
  const { container } = render(
    <ModelPicker
      open
      tier="reasoning"
      selectedProvider="anthropic"
      selectedModelId="claude-opus-4-5"
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onBrowseProviders={vi.fn()}
    />,
  );
  const text = container.textContent ?? "";

  // Case-SENSITIVE on purpose: the slugs are lower-cased, the display names are
  // not, so `"Anthropic"` passes while `"anthropic"` fails. Lower-casing the
  // haystack first would make this assertion impossible to satisfy.
  expect(text).not.toContain("google_genai");
  expect(text).not.toContain("anthropic");
  expect(text).not.toContain("openai");
  expect(text).not.toContain("anthropic_adaptive");
  expect(text).not.toContain("openai_effort");

  expect(screen.getAllByText("Anthropic").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Google").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Adaptive").length).toBeGreaterThan(0);
});

// ── Listbox wiring ─────────────────────────────────────────────────────────

test("the listbox, its options and aria-activedescendant are wired to real nodes", () => {
  renderPicker();

  const list = screen.getByRole("listbox");
  expect(list).toBeInTheDocument();

  const options = screen.getAllByRole("option");
  expect(options.length).toBe(7);
  // `aria-selected` marks the BOUND model, not the keyboard cursor: Opus is
  // bound, and it renders in both the Suggested group and Anthropic's.
  expect(options.filter((el) => el.getAttribute("aria-selected") === "true")).toHaveLength(
    2,
  );

  const activeId = screen.getByRole("combobox").getAttribute("aria-activedescendant");
  expect(activeId).toBeTruthy();
  const activeEl = activeId ? document.getElementById(activeId) : null;
  expect(activeEl).not.toBeNull();
  expect(activeEl).toHaveAttribute("role", "option");
});

// ── The one place colour marks a value ─────────────────────────────────────

test("a context window of 1M or more is coloured, and a smaller one is not", () => {
  renderPicker();

  for (const cell of screen.getAllByText("1M")) {
    expect(cell).toHaveClass("text-j-primary");
  }
  for (const cell of screen.getAllByText("200K")) {
    expect(cell).toHaveClass("text-t-muted");
  }
});

// ── Closed is closed ───────────────────────────────────────────────────────

test("renders nothing while closed", () => {
  const { container } = render(
    <ModelPicker
      open={false}
      tier="reasoning"
      selectedProvider={null}
      selectedModelId={null}
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onBrowseProviders={vi.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { TierCard } from "./tier-card";
import type {
  AgentInfo,
  CatalogModel,
  CatalogProvider,
  ConfigWarning,
  ModelBinding,
} from "@/lib/types";

function model(over: Partial<CatalogModel> = {}): CatalogModel {
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

const GROQ_MODEL = model({
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

const ANTHROPIC: CatalogProvider = {
  provider: "anthropic",
  display_name: "Anthropic",
  auth_kind: "api_key",
  credential_fields: [],
  model_count: 4,
  docs_url: null,
};

const GROQ: CatalogProvider = { ...ANTHROPIC, provider: "groq", display_name: "Groq" };

const AGENTS: AgentInfo[] = [
  { name: "planner", display_name: "Planner", tier: "reasoning" },
  { name: "perceiver", display_name: "Perceiver", tier: "balanced" },
  { name: "librarian", display_name: "Librarian", tier: "balanced" },
  { name: "persona", display_name: "Persona", tier: "fast" },
];

function binding(over: Partial<ModelBinding> = {}): ModelBinding {
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

function warningFor(over: Partial<ConfigWarning> = {}): ConfigWarning {
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

function renderCard(
  over: {
    binding?: Partial<ModelBinding>;
    models?: CatalogModel[];
    providers?: CatalogProvider[];
    agents?: AgentInfo[];
    description?: string;
    dirty?: boolean;
    disabled?: boolean;
    warning?: ConfigWarning;
    rejection?: ConfigWarning;
  } = {},
) {
  const value = binding(over.binding);
  const onChange = vi.fn();
  const onOpenPicker = vi.fn();
  const onConnectProvider = vi.fn();
  render(
    <TierCard
      binding={value}
      models={over.models ?? [model(), GROQ_MODEL]}
      providers={over.providers ?? [ANTHROPIC, GROQ]}
      agents={over.agents ?? AGENTS}
      description={over.description ?? "Deepest reasoning. Slowest, dearest."}
      dirty={over.dirty}
      disabled={over.disabled}
      warning={over.warning}
      rejection={over.rejection}
      onChange={onChange}
      onOpenPicker={onOpenPicker}
      onConnectProvider={onConnectProvider}
    />,
  );
  return { value, onChange, onOpenPicker, onConnectProvider };
}

/** The card element itself — the only thing carrying §9.6's border. */
function card(): HTMLElement {
  const region = document.querySelector("section");
  expect(region).not.toBeNull();
  return region as HTMLElement;
}

const FAST_BINDING: Partial<ModelBinding> = {
  scope_key: "fast",
  provider: "groq",
  model_id: "llama-3.3-70b",
};

// ── §9.6: the unconfigured provider is a consequence, not a status ─────────

test("a warned tier renders the amber card border, the consequence and a Connect action", async () => {
  const { onConnectProvider } = renderCard({
    binding: FAST_BINDING,
    warning: warningFor(),
  });

  expect(card().className).toContain("border-j-warning/35");
  expect(card().className).not.toContain("border-b-secondary");

  expect(
    screen.getByText(/Groq is not connected\./, { exact: false }),
  ).toBeInTheDocument();

  // The slug is what the Providers tab needs; the display name is what the
  // founder reads. The button must carry one and emit the other.
  const connect = screen.getByRole("button", { name: "Connect Groq" });
  await userEvent.click(connect);
  expect(onConnectProvider).toHaveBeenCalledTimes(1);
  expect(onConnectProvider).toHaveBeenCalledWith("groq");
});

// §2.5: there is no tier fallback. Copy that implies one is the defect this
// whole state exists to fix — the founder would wait for a recovery that is
// never coming.
test("the warning copy never promises a fallback", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  const consequence = screen.getByText(/Groq is not connected/).textContent ?? "";
  expect(consequence).not.toMatch(/falls? back to/i);
  expect(consequence).toMatch(/will fail/i);
});

// The server's sentence is preferred, but a warning that arrives without one
// must not render an empty amber row.
test("a warning with no message still states the consequence", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor({ message: "" }) });
  const consequence = screen.getByText(/Groq is not connected/).textContent ?? "";
  expect(consequence).toMatch(/no tier fallback/i);
  expect(consequence).not.toMatch(/falls? back to/i);
});

test("a warned card names the provider by its display name, not its slug", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor({ message: "" }) });
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Connect groq" })).toBeNull();
});

// ── §4.4: a 422 lands on the card that caused it ──────────────────────────

test("a rejection renders on the card and outranks a warning", () => {
  const rejection = warningFor({
    message: "Groq resolves no credential. This binding was not saved.",
  });
  renderCard({
    binding: FAST_BINDING,
    warning: warningFor(),
    rejection,
  });

  expect(screen.getByText(rejection.message)).toBeInTheDocument();
  // The older warning's sentence is gone — one slot, and the newer fact wins.
  expect(screen.queryByText(/every agent on Fast will fail/)).toBeNull();
  expect(card().className).toContain("border-j-warning/35");
});

test("a rejection alone renders without any warning present", () => {
  renderCard({
    binding: FAST_BINDING,
    rejection: warningFor({ message: "Groq resolves no credential." }),
  });
  expect(screen.getByText("Groq resolves no credential.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeInTheDocument();
});

// ── §9.6: substitution, not addition ──────────────────────────────────────

test("the warning replaces the meta row rather than adding a second one", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  expect(screen.queryByText(/per Mtok/)).toBeNull();
  expect(screen.queryByText("Changed — not saved")).toBeNull();
});

// The amber Model-control border lives INSIDE the grid, so it can only be
// asked for from the card. This is the one §9.6 substitution the card cannot
// make itself.
test("the warning is forwarded to the binding grid", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
  expect(screen.getByText("Groq").className).toContain("text-j-warning");
});

test("a rejection recolours the Model control for the same reason a warning does", () => {
  renderCard({
    binding: FAST_BINDING,
    rejection: warningFor({ message: "Refused." }),
  });
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
});

test("a healthy card carries neither the amber border nor a Connect action", () => {
  renderCard();
  expect(card().className).toContain("border-b-secondary");
  expect(card().className).not.toContain("border-j-warning/35");
  expect(screen.queryByRole("button", { name: /^Connect/ })).toBeNull();
  expect(screen.getByLabelText(/^Model/).className).not.toContain(
    "border-j-warning/45",
  );
});

// ── §9.5 meta row: right slot is exclusive ────────────────────────────────

test("a dirty tier shows the changed marker instead of the capability hint", () => {
  renderCard({ dirty: true });
  expect(screen.getByText("Changed — not saved")).toBeInTheDocument();
  expect(screen.queryByText(/do not accept temperature/)).toBeNull();
});

test("a clean tier shows the capability hint instead of the changed marker", () => {
  renderCard();
  expect(
    screen.getByText("Adaptive-thinking models do not accept temperature."),
  ).toBeInTheDocument();
  expect(screen.queryByText("Changed — not saved")).toBeNull();
});

// The hint is a claim about `accepts_temperature`, not about the thinking
// style — a model that DOES accept temperature must never carry it.
test("a model that accepts temperature carries no capability hint", () => {
  renderCard({ binding: FAST_BINDING });
  expect(screen.queryByText(/do not accept temperature/)).toBeNull();
});

// ── §9.5 meta row: sourced from the selected model ────────────────────────

test("the meta row renders context, per-Mtok costs and thinking style", () => {
  renderCard();
  expect(screen.getByText("200K context")).toBeInTheDocument();
  // The catalog stores per-1k; the row states per-Mtok, so 0.005 → $5.
  expect(screen.getByText("$5 / $25 per Mtok")).toBeInTheDocument();
  expect(screen.getByText("Adaptive thinking")).toBeInTheDocument();
});

test("sub-cent per-1k costs survive the ×1000 without rounding to zero", () => {
  renderCard({ binding: FAST_BINDING });
  expect(screen.getByText("128K context")).toBeInTheDocument();
  expect(screen.getByText("$0.59 / $0.79 per Mtok")).toBeInTheDocument();
  expect(screen.getByText("No thinking")).toBeInTheDocument();
});

// A retired model has no price to print. Printing a zero would assert one.
test("a model absent from the catalog says so rather than printing zeroes", () => {
  renderCard({ binding: { model_id: "retired-model" } });
  expect(screen.getByText(/no longer in the catalog/)).toBeInTheDocument();
  expect(screen.queryByText(/per Mtok/)).toBeNull();
});

// ── Header ─────────────────────────────────────────────────────────────────

test("the agent chips list exactly the agents on this tier", () => {
  renderCard({ binding: { scope_key: "balanced" } });
  const chips = screen.getByRole("list", { name: "Agents on Balanced" });
  expect(
    Array.from(chips.querySelectorAll("li")).map((li) => li.textContent),
  ).toEqual(["Perceiver", "Librarian"]);
  expect(screen.queryByText("Planner")).toBeNull();
  expect(screen.queryByText("Persona")).toBeNull();
});

test("a tier with no agents renders no empty chip list", () => {
  renderCard({ binding: { scope_key: "reasoning" }, agents: [] });
  expect(screen.queryByRole("list")).toBeNull();
});

// A3: the heading is sentence case, and it comes from the binding itself — a
// separate `tier` prop could name a tier the grid below is not editing.
test("the card is headed by its own binding's tier, in sentence case", () => {
  renderCard({ binding: { scope_key: "fast", ...FAST_BINDING } });
  expect(screen.getByRole("heading", { name: "Fast" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Fast" })).toBeInTheDocument();
});

test("the description renders beside the tier name", () => {
  renderCard({ description: "Cheap and quick. Classification and triage." });
  expect(
    screen.getByText("Cheap and quick. Classification and triage."),
  ).toBeInTheDocument();
});

// ── Pass-through ───────────────────────────────────────────────────────────

test("the card edits nothing itself — the grid's patches pass straight through", async () => {
  const { value, onChange, onOpenPicker } = renderCard();
  const before = { ...value };
  await userEvent.click(screen.getByLabelText(/^Model/));
  expect(onOpenPicker).toHaveBeenCalledTimes(1);
  await userEvent.selectOptions(screen.getByLabelText("Effort"), "low");
  expect(onChange).toHaveBeenCalledWith({ effort: "low" });
  expect(value).toEqual(before);
});

test("disabled turns off the grid and the Connect action alike", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor(), disabled: true });
  expect(screen.getByLabelText(/^Model/)).toBeDisabled();
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeDisabled();
});

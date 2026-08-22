import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { BindingFields } from "./binding-fields";
import type { CatalogModel, CatalogProvider, ModelBinding } from "@/lib/types";

function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    display_name: "Claude Opus 4.5",
    thinking_style: "adaptive",
    accepts_temperature: true,
    suggested_tier: "reasoning",
    context_window: 200000,
    input_cost_per_1k: 0.005,
    output_cost_per_1k: 0.025,
    supports_prompt_cache: true,
    ...over,
  };
}

const ANTHROPIC: CatalogProvider = {
  provider: "anthropic",
  display_name: "Anthropic",
  auth_kind: "api_key",
  credential_fields: [],
  model_count: 4,
  docs_url: null,
};

function binding(over: Partial<ModelBinding> = {}): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: "reasoning",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    effort: "high",
    max_tokens: 8192,
    temperature: 0.7,
    ...over,
  };
}

function renderFields(
  over: {
    binding?: Partial<ModelBinding>;
    model?: Partial<CatalogModel>;
    dirty?: boolean;
    disabled?: boolean;
  } = {},
) {
  const value = binding(over.binding);
  const onChange = vi.fn();
  const onOpenPicker = vi.fn();
  render(
    <BindingFields
      binding={value}
      models={[model(over.model)]}
      providers={[ANTHROPIC]}
      onChange={onChange}
      onOpenPicker={onOpenPicker}
      dirty={over.dirty}
      disabled={over.disabled}
    />,
  );
  return { value, onChange, onOpenPicker };
}

// ── F4: unsupported controls are disabled, never unmounted ─────────────────
// Unmounting reflowed the whole row on every model change. The assertion is
// deliberately two-part: present AND disabled. `queryBy…` returning null would
// pass a weaker "not editable" test while reintroducing the reflow.

test("temperature renders present-and-disabled when the model does not accept it", () => {
  renderFields({ model: { accepts_temperature: false } });
  const control = screen.getByLabelText("Temperature");
  expect(control).toBeInTheDocument();
  expect(control).toBeDisabled();
  expect(control).toHaveValue("Not accepted");
});

test("temperature is editable when the model accepts it", async () => {
  const { onChange } = renderFields();
  const control = screen.getByLabelText("Temperature");
  expect(control).not.toBeDisabled();
  await userEvent.clear(control);
  expect(onChange).toHaveBeenCalledWith({ temperature: null });
});

// ── B4: `effort: "none"` is a legal contract value ─────────────────────────
// A non-thinking model has no effort to set, but the binding still carries one.
// The disabled control is how that value becomes representable at all.

test("effort renders disabled reading n/a for a non-thinking model", () => {
  renderFields({
    model: { thinking_style: "none" },
    binding: { effort: "none" },
  });
  const control = screen.getByLabelText("Effort");
  expect(control).toBeInTheDocument();
  expect(control).toBeDisabled();
  expect(control).toHaveValue("n/a");
});

test("effort is a live selector for a thinking model", async () => {
  const { onChange } = renderFields();
  const control = screen.getByLabelText("Effort");
  expect(control).not.toBeDisabled();
  await userEvent.selectOptions(control, "low");
  expect(onChange).toHaveBeenCalledWith({ effort: "low" });
});

// ── L2: every control carries a VISIBLE label ──────────────────────────────

test("every control is named by a visible label, not by aria-label alone", () => {
  renderFields();
  // Model's accessible name is "<label> <value>", so it is matched by prefix.
  const controls = [
    screen.getByLabelText(/^Model/),
    screen.getByLabelText("Effort"),
    screen.getByLabelText("Max tokens"),
    screen.getByLabelText("Temperature"),
  ];
  for (const control of controls) {
    expect(control).toBeInTheDocument();
    // The name must come from a rendered <label>, not from an attribute that
    // vanishes the moment the row is read visually. This is the L2 regression.
    expect(control).not.toHaveAttribute("aria-label");
  }
  for (const text of ["Model", "Effort", "Max tokens", "Temperature"]) {
    const label = screen
      .getAllByText(text)
      .find((el) => el.tagName === "LABEL");
    expect(label).toBeTruthy();
    expect(label).toBeVisible();
  }
});

// ── Immutability ───────────────────────────────────────────────────────────

test("editing max tokens emits a patch and does not mutate the binding", async () => {
  const { value, onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const before = { ...value };
  await userEvent.type(screen.getByLabelText("Max tokens"), "4");
  expect(onChange).toHaveBeenCalledWith({ max_tokens: 81924 });
  expect(value).toEqual(before);
  expect(value.max_tokens).toBe(8192);
});

test("max tokens never emits 0 — the backend rejects it", async () => {
  const { onChange } = renderFields({ binding: { max_tokens: 8192 } });
  await userEvent.clear(screen.getByLabelText("Max tokens"));
  expect(onChange).toHaveBeenLastCalledWith({ max_tokens: 1 });
});

// ── The picker, not a dropdown ─────────────────────────────────────────────

test("clicking the model control opens the picker and changes nothing", async () => {
  const { onChange, onOpenPicker } = renderFields();
  await userEvent.click(screen.getByLabelText(/^Model/));
  expect(onOpenPicker).toHaveBeenCalledTimes(1);
  expect(onChange).not.toHaveBeenCalled();
});

test("the model control announces itself as opening a dialog", () => {
  renderFields();
  expect(screen.getByLabelText(/^Model/)).toHaveAttribute(
    "aria-haspopup",
    "dialog",
  );
});

// ── F1's cause: there is no provider control ───────────────────────────────
// The old row put a provider <select> beside the model <select>; changing it
// blanked `model_id` and Save 400d. The provider is now derived and read-only.

test("the grid has no provider control at all", () => {
  renderFields();
  expect(screen.queryByLabelText(/provider/i)).toBeNull();
  // Effort is the ONLY select in the grid — a second one would be the provider.
  expect(screen.getAllByRole("combobox")).toHaveLength(1);
  expect(screen.getAllByRole("combobox")[0]).toBe(
    screen.getByLabelText("Effort"),
  );
});

test("the provider renders as text derived from the selected model", () => {
  // The binding's own slug is lowercase; "Anthropic" can only have come from
  // resolving the selected model's `provider` against the catalog.
  renderFields();
  expect(screen.getByText("Anthropic")).toBeInTheDocument();
});

test("an uncatalogued provider falls back to its slug rather than rendering blank", () => {
  render(
    <BindingFields
      binding={binding({ provider: "groq", model_id: "llama-3.3-70b" })}
      models={[model({ provider: "groq", model_id: "llama-3.3-70b" })]}
      providers={[]}
      onChange={vi.fn()}
      onOpenPicker={vi.fn()}
    />,
  );
  expect(screen.getByText("groq")).toBeInTheDocument();
});

// ── Dirty / disabled ───────────────────────────────────────────────────────

test("dirty applies the changed-control styling", () => {
  renderFields({ dirty: true });
  const maxTokens = screen.getByLabelText("Max tokens");
  expect(maxTokens.className).toContain("border-j-primary");
  expect(maxTokens.className).toContain(
    "shadow-[0_0_0_1px_var(--muldro-primary-soft)]",
  );
  expect(maxTokens.className).not.toContain("border-b-secondary");
});

test("a clean binding carries the idle border, not the changed one", () => {
  renderFields();
  const maxTokens = screen.getByLabelText("Max tokens");
  expect(maxTokens.className).toContain("border-b-secondary");
  expect(maxTokens.className).not.toContain("border-j-primary");
});

test("disabled turns off every editable control without unmounting any", () => {
  renderFields({ disabled: true });
  expect(screen.getByLabelText(/^Model/)).toBeDisabled();
  expect(screen.getByLabelText("Effort")).toBeDisabled();
  expect(screen.getByLabelText("Max tokens")).toBeDisabled();
  expect(screen.getByLabelText("Temperature")).toBeDisabled();
});

// ── Fail-closed on an unresolvable model ───────────────────────────────────

test("a model absent from the catalog disables both capability controls", () => {
  renderFields({ binding: { model_id: "retired-model" } });
  expect(screen.getByLabelText("Effort")).toBeDisabled();
  expect(screen.getByLabelText("Temperature")).toBeDisabled();
  // …and still names the binding rather than showing an empty box.
  expect(screen.getByText("retired-model")).toBeInTheDocument();
});

test("an empty binding invites a choice instead of rendering blank", () => {
  renderFields({ binding: { model_id: "" } });
  expect(screen.getByText("Select a model…")).toBeInTheDocument();
});

// ── §9.6 warning variant ───────────────────────────────────────────────────

test("the warning variant recolours the model control without unmounting anything", () => {
  render(
    <BindingFields
      binding={binding()}
      models={[model()]}
      providers={[ANTHROPIC]}
      onChange={vi.fn()}
      onOpenPicker={vi.fn()}
      warning
    />,
  );
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
  expect(screen.getByText("Anthropic").className).toContain("text-j-warning");
  expect(screen.getByLabelText("Max tokens")).toBeInTheDocument();
});

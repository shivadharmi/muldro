import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { test, expect, vi } from "vitest";

import { BindingFields } from "./binding-fields";
import type { BindingPatch } from "./binding-fields";
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
    warning?: boolean;
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
      warning={over.warning}
    />,
  );
  return { value, onChange, onOpenPicker };
}

/**
 * The grid wired to a real controlled parent.
 *
 * `vi.fn()` alone cannot see the defect class these fields are most prone to:
 * an emitted patch merges into the draft and comes straight back down as the
 * displayed value, so a handler that maps an intermediate keystroke to a
 * "safe" number rewrites the field under the founder's cursor. That round trip
 * only exists when something actually feeds the patch back.
 */
function Harness({
  initial,
  onChange,
  models = [model()],
}: {
  initial: ModelBinding;
  onChange: (patch: BindingPatch) => void;
  models?: CatalogModel[];
}) {
  const [current, setCurrent] = useState(initial);
  return (
    <BindingFields
      binding={current}
      models={models}
      providers={[ANTHROPIC]}
      onChange={(patch) => {
        onChange(patch);
        setCurrent((prev) => ({ ...prev, ...patch }));
      }}
      onOpenPicker={vi.fn()}
    />
  );
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

// A stored 0 is a real temperature, not an absent one. `||` would swallow it.
test("a stored temperature of 0 renders as 0, not as empty", () => {
  renderFields({ binding: { temperature: 0 } });
  expect(screen.getByLabelText("Temperature")).toHaveValue(0);
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

// A3: a select must not announce raw slugs.
test("effort options are sentence case, not raw slugs", () => {
  renderFields();
  expect(screen.getByRole("option", { name: "Medium" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "medium" })).toBeNull();
});

// ── An unresolvable model is UNKNOWN, not unsupported ──────────────────────
// `n/a` and `Not accepted` are §4.3's claims about a capability we have looked
// up. Printing them for a retired model asserts something unestablished and
// hides the values actually stored on the binding.

test("a model absent from the catalog shows its stored values, not a capability claim", () => {
  renderFields({
    binding: { model_id: "retired-model", effort: "high", temperature: 0.7 },
  });
  const effort = screen.getByLabelText("Effort");
  const temperature = screen.getByLabelText("Temperature");
  expect(effort).toBeDisabled();
  expect(temperature).toBeDisabled();
  expect(effort).toHaveValue("High");
  expect(temperature).toHaveValue("0.7");
  expect(screen.queryByDisplayValue("n/a")).toBeNull();
  expect(screen.queryByDisplayValue("Not accepted")).toBeNull();
  // …and the model itself is named rather than shown as an empty box.
  expect(screen.getByText("retired-model")).toBeInTheDocument();
});

test("a stored temperature of 0 survives an unresolvable model", () => {
  renderFields({ binding: { model_id: "retired-model", temperature: 0 } });
  expect(screen.getByLabelText("Temperature")).toHaveValue("0");
});

test("a null temperature on an unresolvable model reads as unset", () => {
  renderFields({ binding: { model_id: "retired-model", temperature: null } });
  expect(screen.getByLabelText("Temperature")).toHaveValue("—");
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
    const label = screen.getAllByText(text).find((el) => el.tagName === "LABEL");
    expect(label).toBeTruthy();
    expect(label).toBeVisible();
  }
});

// ── Max tokens: no silent rewrite ──────────────────────────────────────────

test("editing max tokens emits a patch and does not mutate the binding", async () => {
  const { value, onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const before = { ...value };
  await userEvent.type(screen.getByLabelText("Max tokens"), "4");
  expect(onChange).toHaveBeenCalledWith({ max_tokens: 81924 });
  expect(value).toEqual(before);
  expect(value.max_tokens).toBe(8192);
});

// The regression this whole draft mechanism exists for: clearing the field to
// retype it must not emit a "safe" number that comes back as the field's value.
test("clearing max tokens emits nothing and leaves the field empty", async () => {
  const { onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const input = screen.getByLabelText("Max tokens");
  await userEvent.clear(input);
  expect(onChange).not.toHaveBeenCalled();
  expect(input).toHaveValue(null);
});

test("clearing and retyping max tokens lands the typed value, through a real parent", async () => {
  const onChange = vi.fn();
  render(<Harness initial={binding({ max_tokens: 8192 })} onChange={onChange} />);
  const input = screen.getByLabelText("Max tokens");
  await userEvent.clear(input);
  await userEvent.type(input, "16384");
  // Not 116384, and not 1 — the field never rewrote itself mid-edit.
  expect(input).toHaveValue(16384);
  expect(onChange).toHaveBeenLastCalledWith({ max_tokens: 16384 });
});

test("a fractional max tokens is never emitted — the column is an int", async () => {
  const onChange = vi.fn();
  render(<Harness initial={binding({ max_tokens: 8192 })} onChange={onChange} />);
  const input = screen.getByLabelText("Max tokens");
  await userEvent.clear(input);
  await userEvent.type(input, "1.5");
  for (const [patch] of onChange.mock.calls) {
    expect(Number.isInteger(patch.max_tokens)).toBe(true);
  }
  expect(onChange).not.toHaveBeenCalledWith({ max_tokens: 1.5 });
});

test("max tokens never emits 0 — the backend rejects it", async () => {
  const { onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const input = screen.getByLabelText("Max tokens");
  await userEvent.clear(input);
  await userEvent.type(input, "0");
  expect(onChange).not.toHaveBeenCalled();
});

test("blurring a half-typed max tokens reverts to the stored value", async () => {
  const { onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const input = screen.getByLabelText("Max tokens");
  await userEvent.clear(input);
  await userEvent.tab();
  expect(onChange).not.toHaveBeenCalled();
  expect(input).toHaveValue(8192);
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
  expect(screen.getAllByRole("combobox")[0]).toBe(screen.getByLabelText("Effort"));
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

// ── Dirty / warning / disabled ─────────────────────────────────────────────

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

test("the warning variant recolours the model control without unmounting anything", () => {
  renderFields({ warning: true });
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
  expect(screen.getByText("Anthropic").className).toContain("text-j-warning");
  expect(screen.getByLabelText("Max tokens")).toBeInTheDocument();
});

// Rebinding a warned tier is the whole point of the warned tier. The control
// the founder just changed must not be the one control giving no feedback.
test("a warned binding still shows the changed ring on the model control", () => {
  renderFields({ warning: true, dirty: true });
  const modelControl = screen.getByLabelText(/^Model/);
  expect(modelControl.className).toContain("border-j-warning/45");
  expect(modelControl.className).toContain(
    "shadow-[0_0_0_1px_var(--muldro-primary-soft)]",
  );
});

test("disabled turns off every editable control without unmounting any", () => {
  renderFields({ disabled: true });
  expect(screen.getByLabelText(/^Model/)).toBeDisabled();
  expect(screen.getByLabelText("Effort")).toBeDisabled();
  expect(screen.getByLabelText("Max tokens")).toBeDisabled();
  expect(screen.getByLabelText("Temperature")).toBeDisabled();
});

test("an empty binding invites a choice instead of rendering blank", () => {
  renderFields({ binding: { model_id: "" } });
  expect(screen.getByText("Select a model…")).toBeInTheDocument();
});

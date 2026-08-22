import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { BindingFields } from "./binding-fields";
import { binding, model, renderFields } from "./binding-fields-fixtures";

/**
 * What the grid SHOWS. The emit rules for the two numeric fields — which raw
 * strings become a patch — live in `binding-fields-numeric.test.tsx`.
 */

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

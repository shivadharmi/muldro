import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { Harness, binding, renderFields } from "./binding-fields-fixtures";

/**
 * What a keystroke MEANS in the two numeric fields.
 *
 * Both hold their raw text in a draft and emit a patch only for a string that
 * is genuinely complete, because a controlled input hands whatever it emits
 * straight back down as its own displayed value. The two fields are not the
 * same problem, though, and that is what these specs pin:
 *
 *   * Max tokens is an integer, so no valid input passes through an
 *     unparseable prefix — `""` means "cleared" or "garbage" and both want the
 *     same answer, emit nothing.
 *   * Temperature is a decimal, and typing one goes THROUGH an incomplete
 *     literal every time. `""` there means "cleared" (emit `null`) or
 *     "mid-decimal" (emit nothing) — opposite answers from one DOM value,
 *     which is why that control is a text input.
 */

// ── Max tokens ─────────────────────────────────────────────────────────────

test("editing max tokens emits a patch and does not mutate the binding", async () => {
  const { value, onChange } = renderFields({ binding: { max_tokens: 8192 } });
  const before = { ...value };
  await userEvent.type(screen.getByLabelText("Max tokens"), "4");
  expect(onChange).toHaveBeenCalledWith({ max_tokens: 81924 });
  expect(value).toEqual(before);
  expect(value.max_tokens).toBe(8192);
});

// The regression the draft exists for: clearing the field to retype it must not
// emit a "safe" number that comes back as the field's value.
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

// ── Temperature ────────────────────────────────────────────────────────────
// These are driven with `fireEvent.change`, NOT `userEvent.type`. jsdom's
// user-event does not deliver the `"0."` keystroke at all — it goes straight
// from `"0"` to `"0.7"` — so a character-by-character test passes against the
// broken handler and pins nothing. `fireEvent.change` delivers the intermediate
// the browser actually sends.

test("a stored temperature of 0 renders as 0, not as empty", () => {
  renderFields({ binding: { temperature: 0 } });
  const control = screen.getByLabelText("Temperature");
  expect(control).toHaveValue("0");
  expect(control).not.toHaveValue("");
});

test("clearing a set temperature emits null", async () => {
  const { onChange } = renderFields();
  const control = screen.getByLabelText("Temperature");
  expect(control).not.toBeDisabled();
  await userEvent.clear(control);
  expect(onChange).toHaveBeenCalledWith({ temperature: null });
});

test("an incomplete decimal emits nothing, while a cleared field emits null", () => {
  const { onChange } = renderFields({ binding: { temperature: 0.7 } });
  const input = screen.getByLabelText("Temperature");

  // These two are the SAME DOM value under `type="number"` (both sanitize to
  // ""), which is why that type could not carry this field.
  fireEvent.change(input, { target: { value: "0." } });
  expect(onChange).not.toHaveBeenCalled();

  fireEvent.change(input, { target: { value: "" } });
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ temperature: null });
});

test("typing a decimal temperature through a real parent lands the typed value", () => {
  const onChange = vi.fn();
  render(<Harness initial={binding({ temperature: null })} onChange={onChange} />);
  const input = screen.getByLabelText("Temperature");
  for (const raw of ["0", "0.", "0.7"]) {
    fireEvent.change(input, { target: { value: raw } });
  }
  expect(input).toHaveValue("0.7");
  expect(onChange).toHaveBeenLastCalledWith({ temperature: 0.7 });
  // The two values the old handler produced on the way through: it emitted
  // `null` for "0.", which blanked the field, so the next keystroke read as 7.
  expect(onChange).not.toHaveBeenCalledWith({ temperature: 7 });
  expect(onChange).not.toHaveBeenCalledWith({ temperature: null });
});

test.each(["0.", ".", "-", "1e", "-0.5", "abc"])(
  "an unparseable or negative temperature %j is never emitted",
  (raw) => {
    const { onChange } = renderFields({ binding: { temperature: 0.7 } });
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: raw },
    });
    expect(onChange).not.toHaveBeenCalled();
  },
);

test("blurring a half-typed decimal reverts to the stored temperature", () => {
  const { onChange } = renderFields({ binding: { temperature: 0.7 } });
  const input = screen.getByLabelText("Temperature");
  fireEvent.change(input, { target: { value: "0." } });
  expect(input).toHaveValue("0.");
  // Abandoning a half-typed decimal must not leave the binding unset — the
  // draft is discarded and the stored value comes back.
  fireEvent.blur(input);
  expect(onChange).not.toHaveBeenCalled();
  expect(input).toHaveValue("0.7");
});

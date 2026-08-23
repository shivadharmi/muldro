import { test, expect } from "vitest";

import {
  EFFORT_LABELS,
  EFFORT_OPTIONS,
  findModel,
  isCompleteDecimal,
  isCompleteTokenCount,
  toEffort,
} from "./binding-values";
import { binding, model } from "./binding-fields-fixtures";

/**
 * The value rules, tested directly.
 *
 * `isCompleteDecimal` in particular decides what a keystroke MEANS, and getting
 * it wrong does not throw — it silently saves a sampling temperature nobody
 * chose. It is reachable through the rendered grid, but a rule this sharp
 * deserves its own case table rather than a handful of incidental renders.
 */

// ── isCompleteDecimal ──────────────────────────────────────────────────────

test.each([
  // [raw, complete?, why]
  ["0.7", true, "the ordinary case"],
  [".5", true, "a leading separator is a complete literal"],
  ["07", true, "a redundant leading zero still parses to 7"],
  ["0", true, "zero is a real temperature, not an absent one"],
  ["1.25", true, "more than one decimal place"],
  ["0.", false, "the halfway state of typing 0.7 — Number() says 0, we say no"],
  [".", false, "a bare separator"],
  ["-", false, "a bare sign"],
  ["1e", false, "a truncated exponent"],
  ["-0.5", false, "negative — this is where the min={0} guard now lives"],
  ["", false, "empty is the caller's branch (a legal null), not a number"],
  ["abc", false, "not a number at all"],
  [" 0.7", false, "no surrounding whitespace"],
  ["0.7.1", false, "two separators"],
])("isCompleteDecimal(%j) is %s — %s", (raw, expected) => {
  expect(isCompleteDecimal(raw)).toBe(expected);
});

// The trap that makes the regex necessary rather than decorative: a
// `Number.isFinite` gate would have accepted "0." and emitted a silent 0.
test("Number() alone would accept the one string the rule exists to reject", () => {
  expect(Number.isFinite(Number("0."))).toBe(true);
  expect(Number("0.")).toBe(0);
  expect(isCompleteDecimal("0.")).toBe(false);
});

// ── isCompleteTokenCount ───────────────────────────────────────────────────

test.each([
  ["8192", true, "the ordinary case"],
  ["1", true, "the floor"],
  ["0", false, "the backend rejects max_tokens < 1"],
  ["1.5", false, "the column is an int"],
  ["-1", false, "negative"],
  ["", false, "no legal empty state — cleared and garbage want the same answer"],
  ["abc", false, "not a number"],
  [" ", false, "blank is not zero, however Number() reads it"],
])("isCompleteTokenCount(%j) is %s — %s", (raw, expected) => {
  expect(isCompleteTokenCount(raw)).toBe(expected);
});

// The asymmetry between the two fields, stated as an assertion rather than only
// in prose: "" is the one input on which they deliberately disagree.
test("the two predicates disagree on empty, and that is the whole difference", () => {
  expect(isCompleteTokenCount("")).toBe(false);
  expect(isCompleteDecimal("")).toBe(false);
  // …but temperature's caller turns that `false` into an explicit `null`, while
  // max tokens' caller emits nothing. Pinned end-to-end in
  // `binding-fields-numeric.test.tsx`.
});

// ── toEffort ───────────────────────────────────────────────────────────────

test.each(EFFORT_OPTIONS)("toEffort round-trips the legal value %s", (effort) => {
  expect(toEffort(effort, "high")).toBe(effort);
});

test("toEffort falls back rather than asserting an unknown string into the union", () => {
  expect(toEffort("turbo", "medium")).toBe("medium");
  expect(toEffort("", "low")).toBe("low");
});

test("every effort option has a sentence-case label — A3", () => {
  for (const effort of EFFORT_OPTIONS) {
    const label = EFFORT_LABELS[effort];
    expect(label).toBeTruthy();
    expect(label).not.toBe(effort);
    expect(label[0]).toBe(label[0].toUpperCase());
  }
});

// ── findModel ──────────────────────────────────────────────────────────────

test("findModel matches on provider AND model_id", () => {
  const anthropic = model();
  const impostor = model({ provider: "bedrock", display_name: "Impostor" });
  // Same model_id, different provider — a model_id is not unique on its own.
  expect(findModel([impostor, anthropic], binding())).toBe(anthropic);
});

test("findModel returns undefined for a de-listed or empty binding", () => {
  expect(findModel([model()], binding({ model_id: "retired" }))).toBeUndefined();
  expect(findModel([model()], binding({ model_id: "" }))).toBeUndefined();
  expect(findModel([], binding())).toBeUndefined();
});

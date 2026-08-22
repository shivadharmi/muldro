/**
 * The value rules behind the binding grid: which strings a keystroke may become,
 * what a capability the catalog cannot describe should read as, and how a
 * binding finds its model. React-free, so each rule is testable directly rather
 * than only through a rendered grid.
 *
 * The emit predicates below exist because of one property, and it is worth
 * stating before the code: **a valid value's typing path can pass through a DOM
 * state the handler cannot distinguish from a different meaningful value.** Both
 * numeric controls therefore hold their raw text and consult a predicate here
 * before emitting anything.
 */

import type { CatalogModel, ModelBinding } from "@/lib/types";

// ── Effort ─────────────────────────────────────────────────────────────────

export const EFFORT_OPTIONS: readonly ModelBinding["effort"][] = [
  "none",
  "low",
  "medium",
  "high",
];

/** Sentence case, per **A3** — a select must not announce a raw slug. */
export const EFFORT_LABELS: Record<ModelBinding["effort"], string> = {
  none: "None",
  low: "Low",
  medium: "Medium",
  high: "High",
};

/** Narrow a select's raw string back to the union without an `as`. The options
 *  come from `EFFORT_OPTIONS`, so the fallback is unreachable — but it is the
 *  lookup, not an assertion, that makes that true. */
export function toEffort(
  value: string,
  fallback: ModelBinding["effort"],
): ModelBinding["effort"] {
  return EFFORT_OPTIONS.find((option) => option === value) ?? fallback;
}

// ── Disabled-control copy ──────────────────────────────────────────────────

/** §4.3's two fixed strings. Each is a claim about a KNOWN capability, so
 *  neither may be shown for a model whose capabilities we cannot look up — an
 *  unresolvable model shows its STORED value instead. */
export const EFFORT_UNSUPPORTED = "n/a";
export const TEMPERATURE_UNSUPPORTED = "Not accepted";
/** What a null temperature reads as when the control is not editable. */
export const TEMPERATURE_UNSET = "—";

// ── Emit rules ─────────────────────────────────────────────────────────────

/**
 * A COMPLETE non-negative decimal literal — the only shape Temperature emits.
 *
 * `"0."` is excluded even though `Number("0.")` is a perfectly finite `0`: it is
 * the halfway state of typing `"0.7"`, not a value, and emitting `0` there would
 * push a sampling temperature the founder never chose. `"."`, `"-"`, `"1e"` and
 * `"-0.5"` are excluded too, which is where the "no negative temperature" guard
 * lives — a real check, rather than the `min={0}` attribute it replaces, which
 * did nothing on a text input and never blocked typed values on a number one.
 */
export const COMPLETE_DECIMAL = /^\d*\.?\d+$/;

/** Whether `raw` is a temperature worth emitting. `""` is deliberately NOT
 *  handled here: an empty temperature is a legal `null`, so the caller owns that
 *  branch, and this predicate answers only "is this a finished number?". */
export function isCompleteDecimal(raw: string): boolean {
  return COMPLETE_DECIMAL.test(raw);
}

/**
 * Whether `raw` is a max-token count the backend can store — the column is an
 * `int` with a floor of 1.
 *
 * Unlike temperature, `""` IS rejected here rather than delegated: max tokens
 * has no legal empty state, so "cleared" and "garbage" want the same answer.
 * That asymmetry is the whole difference between the two fields.
 */
export function isCompleteTokenCount(raw: string): boolean {
  if (raw === "") return false;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed >= 1;
}

// ── Lookup ─────────────────────────────────────────────────────────────────

/** The binding's model, identified by BOTH keys — a bare `model_id` is not
 *  unique across providers. `undefined` for an empty or de-listed binding. */
export function findModel(
  models: readonly CatalogModel[],
  binding: ModelBinding,
): CatalogModel | undefined {
  return models.find(
    (m) => m.provider === binding.provider && m.model_id === binding.model_id,
  );
}

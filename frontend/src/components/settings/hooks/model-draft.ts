import type { ModelBinding, ModelConfig } from "@/lib/types";

/** The editable half of a {@link ModelConfig}. `providers` and `warnings` are
 *  server-owned and therefore never part of the draft. */
export interface ModelDraft {
  tiers: ModelBinding[];
  agent_overrides: ModelBinding[];
}

export type DraftListKey = "tiers" | "agent_overrides";

const LIST_KEYS: readonly DraftListKey[] = ["tiers", "agent_overrides"];

/** Deeply frozen: this singleton is handed to every consumer that renders
 *  before `load()` resolves, so ONE in-place `draft.tiers.sort(...)` would
 *  corrupt it process-wide (tests included). The repo's immutability rule is
 *  enforced here rather than trusted. */
export const EMPTY_DRAFT: ModelDraft = Object.freeze({
  tiers: Object.freeze([] as ModelBinding[]) as ModelBinding[],
  agent_overrides: Object.freeze([] as ModelBinding[]) as ModelBinding[],
});

export function listKeyFor(
  scopeType: ModelBinding["scope_type"],
): DraftListKey {
  return scopeType === "tier" ? "tiers" : "agent_overrides";
}

/** Stable identity of one binding across the draft, the saved config, the
 *  server's `warnings`, and a 422's bind rejections. */
export function bindingKey(
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): string {
  return `${scopeType}:${scopeKey}`;
}

export function indexOfBinding(
  list: readonly ModelBinding[],
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): number {
  return list.findIndex(
    (b) => b.scope_type === scopeType && b.scope_key === scopeKey,
  );
}

/** Structural comparison of the seven fields a binding actually carries.
 *  Deliberately explicit rather than `JSON.stringify` — key order in a spread
 *  copy is not guaranteed to match the server's, and a stringify diff would
 *  then report a clean binding as dirty. */
export function bindingsEqual(a: ModelBinding, b: ModelBinding): boolean {
  return (
    a.scope_type === b.scope_type &&
    a.scope_key === b.scope_key &&
    a.provider === b.provider &&
    a.model_id === b.model_id &&
    a.effort === b.effort &&
    a.max_tokens === b.max_tokens &&
    a.temperature === b.temperature
  );
}

/** A fresh draft off a saved config. New arrays every time, so the returned
 *  draft shares no mutable structure with the config it came from. */
export function draftFrom(config: ModelConfig | null): ModelDraft {
  if (!config) return EMPTY_DRAFT;
  return {
    tiers: [...config.tiers],
    agent_overrides: [...config.agent_overrides],
  };
}

function indexDraft(draft: ModelDraft): Map<string, ModelBinding> {
  const index = new Map<string, ModelBinding>();
  for (const listKey of LIST_KEYS) {
    for (const b of draft[listKey]) {
      index.set(bindingKey(b.scope_type, b.scope_key), b);
    }
  }
  return index;
}

/**
 * Every key on which `draft` differs from `baseline`. TOTAL over the diff, not
 * just the half you can see from one side: edits and additions come from
 * walking the draft, REMOVALS from walking the baseline afterwards. Without the
 * second pass an override pending deletion reads as `dirtyCount === 0` and the
 * save bar hides the very edit it should be offering to save.
 */
export function dirtyKeysOf(
  baseline: ModelDraft,
  draft: ModelDraft,
): Set<string> {
  const keys = new Set<string>();
  const before = indexDraft(baseline);
  const after = indexDraft(draft);

  for (const [key, binding] of after) {
    const saved = before.get(key);
    if (!saved || !bindingsEqual(saved, binding)) keys.add(key);
  }
  for (const key of before.keys()) {
    if (!after.has(key)) keys.add(key);
  }
  return keys;
}

/**
 * Adopt `next` (fresh server truth) as the draft WITHOUT discarding the user's
 * pending edits: every binding still clean relative to `baseline` takes the
 * server's value, every dirty one keeps the user's.
 *
 * The same rule serves both places a new config arrives:
 *  - a credential mutation refetched the config (`baseline` = the last saved
 *    config), and
 *  - a save returned (`baseline` = the payload that was submitted, so "dirty"
 *    means precisely "edited while the PUT was in flight").
 *
 * A pending removal survives as an omission; a draft-only addition the server
 * does not know about yet is appended at the end of its list.
 */
export function rebaseDraft(
  baseline: ModelDraft,
  draft: ModelDraft,
  next: ModelDraft,
): ModelDraft {
  const dirty = dirtyKeysOf(baseline, draft);
  const current = indexDraft(draft);
  const result: ModelDraft = { tiers: [], agent_overrides: [] };
  const seen = new Set<string>();

  for (const listKey of LIST_KEYS) {
    for (const b of next[listKey]) {
      const key = bindingKey(b.scope_type, b.scope_key);
      seen.add(key);
      if (!dirty.has(key)) {
        result[listKey].push(b);
        continue;
      }
      const pending = current.get(key);
      // `pending === undefined` means the user removed it — keep it removed.
      if (pending) result[listKey].push(pending);
    }
  }

  for (const listKey of LIST_KEYS) {
    for (const b of draft[listKey]) {
      const key = bindingKey(b.scope_type, b.scope_key);
      if (!seen.has(key) && dirty.has(key)) result[listKey].push(b);
    }
  }
  return result;
}

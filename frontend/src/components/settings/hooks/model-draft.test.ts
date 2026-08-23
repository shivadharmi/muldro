import { expect, test } from "vitest";

import type { ModelBinding } from "@/lib/types";
import {
  EMPTY_DRAFT,
  type ModelDraft,
  bindingKey,
  bindingsEqual,
  dirtyKeysOf,
  draftFrom,
  indexOfBinding,
  listKeyFor,
  rebaseDraft,
} from "./model-draft";
import { binding, makeConfig } from "./model-config-fixtures";

function draft(
  tiers: ReturnType<typeof binding>[],
  agents: ReturnType<typeof binding>[] = [],
): ModelDraft {
  return { tiers, agent_overrides: agents };
}

test("EMPTY_DRAFT and both of its arrays are frozen", () => {
  expect(Object.isFrozen(EMPTY_DRAFT)).toBe(true);
  expect(Object.isFrozen(EMPTY_DRAFT.tiers)).toBe(true);
  expect(Object.isFrozen(EMPTY_DRAFT.agent_overrides)).toBe(true);
  // A shared singleton must survive an in-place mutation attempt.
  expect(() => EMPTY_DRAFT.tiers.push(binding("tier", "fast"))).toThrow();
  expect(EMPTY_DRAFT.tiers).toHaveLength(0);
});

test("listKeyFor, bindingKey and indexOfBinding agree on identity", () => {
  expect(listKeyFor("tier")).toBe("tiers");
  expect(listKeyFor("agent")).toBe("agent_overrides");
  expect(bindingKey("tier", "fast")).toBe("tier:fast");
  expect(bindingKey("agent", "fast")).toBe("agent:fast");

  const list = [binding("tier", "reasoning"), binding("tier", "fast")];
  expect(indexOfBinding(list, "tier", "fast")).toBe(1);
  // Same scope_key, different scope_type, is a DIFFERENT binding.
  expect(indexOfBinding(list, "agent", "fast")).toBe(-1);
});

test("bindingsEqual compares every field a binding carries", () => {
  const base = binding("tier", "fast");
  expect(bindingsEqual(base, binding("tier", "fast"))).toBe(true);

  const fields: Partial<ModelBinding>[] = [
    { provider: "openai" },
    { model_id: "gpt-5" },
    { effort: "high" },
    { max_tokens: 9 },
    { temperature: 0.5 },
  ];
  for (const patch of fields) {
    expect(bindingsEqual(base, { ...base, ...patch })).toBe(false);
  }
});

test("draftFrom copies the arrays and returns EMPTY_DRAFT for null", () => {
  const config = makeConfig();
  const d = draftFrom(config);
  expect(d.tiers).toEqual(config.tiers);
  expect(d.tiers).not.toBe(config.tiers);
  expect(draftFrom(null)).toBe(EMPTY_DRAFT);
});

test("dirtyKeysOf reports edits, additions AND removals", () => {
  const baseline = draft(
    [binding("tier", "reasoning"), binding("tier", "fast")],
    [binding("agent", "planner")],
  );
  const edited = draft(
    [
      binding("tier", "reasoning"),
      binding("tier", "fast", { effort: "high" }),
      binding("tier", "extra"),
    ],
    [],
  );

  expect(dirtyKeysOf(baseline, edited)).toEqual(
    new Set([
      "tier:fast", // edited
      "tier:extra", // added
      "agent:planner", // REMOVED — invisible to a draft-only walk
    ]),
  );
  expect(dirtyKeysOf(baseline, baseline)).toEqual(new Set());
});

test("rebaseDraft takes the server's value for clean bindings", () => {
  const baseline = draft([binding("tier", "reasoning"), binding("tier", "fast")]);
  const next = draft([
    binding("tier", "reasoning", { model_id: "server-choice" }),
    binding("tier", "fast", { model_id: "server-choice" }),
  ]);

  expect(rebaseDraft(baseline, baseline, next)).toEqual(next);
});

test("rebaseDraft preserves a pending edit while rebasing its siblings", () => {
  const baseline = draft([binding("tier", "reasoning"), binding("tier", "fast")]);
  const pending = draft([
    binding("tier", "reasoning"),
    binding("tier", "fast", { provider: "openai" }),
  ]);
  const next = draft([
    binding("tier", "reasoning", { model_id: "server-choice" }),
    binding("tier", "fast", { model_id: "server-choice" }),
  ]);

  const result = rebaseDraft(baseline, pending, next);
  expect(result.tiers[0].model_id).toBe("server-choice"); // clean → rebased
  expect(result.tiers[1].provider).toBe("openai"); // dirty → preserved
  expect(result.tiers[1].model_id).toBe("claude-sonnet");
});

test("rebaseDraft keeps a pending removal removed and a pending addition added", () => {
  const baseline = draft([binding("tier", "fast")], [binding("agent", "planner")]);
  const pending = draft(
    [binding("tier", "fast"), binding("tier", "extra")],
    [], // planner removed
  );
  const next = draft([binding("tier", "fast")], [binding("agent", "planner")]);

  const result = rebaseDraft(baseline, pending, next);
  expect(result.agent_overrides).toEqual([]);
  expect(result.tiers.map((b) => b.scope_key)).toEqual(["fast", "extra"]);
});

test("rebaseDraft moves a dirty binding the server dropped to the end", () => {
  const baseline = draft([
    binding("tier", "a"),
    binding("tier", "b"),
    binding("tier", "c"),
  ]);
  const pending = draft([
    binding("tier", "a"),
    binding("tier", "b", { effort: "high" }),
    binding("tier", "c"),
  ]);
  const next = draft([binding("tier", "a"), binding("tier", "c")]);

  // Documented, not accidental: order follows `next`, dirty leftovers append.
  const result = rebaseDraft(baseline, pending, next);
  expect(result.tiers.map((b) => b.scope_key)).toEqual(["a", "c", "b"]);
  expect(result.tiers[2].effort).toBe("high");
});

test("rebaseDraft drops a clean binding the server no longer has", () => {
  const baseline = draft([binding("tier", "fast"), binding("tier", "gone")]);
  const next = draft([binding("tier", "fast")]);

  const result = rebaseDraft(baseline, baseline, next);
  expect(result.tiers.map((b) => b.scope_key)).toEqual(["fast"]);
});

test("rebaseDraft allocates fresh arrays and never mutates its inputs", () => {
  const baseline = draft([binding("tier", "fast")]);
  const pending = draft([binding("tier", "fast", { effort: "high" })]);
  const next = draft([binding("tier", "fast")]);
  const nextTiers = next.tiers;

  const result = rebaseDraft(baseline, pending, next);
  expect(result).not.toBe(next);
  expect(result.tiers).not.toBe(nextTiers);
  expect(next.tiers).toHaveLength(1);
  expect(next.tiers[0].effort).toBe("medium");
  expect(pending.tiers[0].effort).toBe("high");
});

/**
 * The store is keyed on frame.key, which is the whole point.
 *
 * The surface store keyed on a minted `surf_ULID`, so three polls of one email
 * thread produced three ids and three cards (spec §1 defect 1). frame.key is
 * `source:entity_type:entity_id` — supplied by the source system, stable by
 * construction — so the second message on a thread UPDATES the card.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { useUnitStore } from "./unit-store";
import type { Unit } from "@/lib/types/unit";

const NOW = "2026-08-22T12:00:00Z";

function unit(key: string, overrides: Partial<Unit["frame"]> = {}): Unit {
  return {
    frame: {
      key,
      group_key: null,
      kind: "proposal",
      status: "needs_you",
      headline: `Thing ${key}`,
      source: "gmail",
      entity_type: "email_thread",
      occurred_at: NOW,
      updated_at: NOW,
      importance: 0,
      event_count: 1,
      affordances: [],
      ...overrides,
    },
    body: "",
    quotes: [],
  };
}

describe("useUnitStore", () => {
  beforeEach(() => {
    useUnitStore.setState({ units: [], activeKey: null, detailOpen: false });
  });

  it("starts empty", () => {
    expect(useUnitStore.getState().units).toEqual([]);
  });

  it("appends a new unit", () => {
    useUnitStore.getState().upsertUnit(unit("a:b:c"));
    expect(useUnitStore.getState().units).toHaveLength(1);
  });

  it("replaces a unit with the same key in place, preserving order", () => {
    useUnitStore.getState().upsertUnit(unit("a:b:c"));
    useUnitStore.getState().upsertUnit(unit("d:e:f"));
    useUnitStore.getState().upsertUnit(unit("a:b:c", { event_count: 5 }));
    const units = useUnitStore.getState().units;
    expect(units.map((u) => u.frame.key)).toEqual(["a:b:c", "d:e:f"]);
    expect(units[0].frame.event_count).toBe(5);
  });

  it("ignores a unit with no key", () => {
    // A malformed WS frame must not blank the workspace.
    useUnitStore.getState().upsertUnit({ frame: {} } as unknown as Unit);
    expect(useUnitStore.getState().units).toEqual([]);
  });

  it("ignores a null unit", () => {
    useUnitStore.getState().upsertUnit(null as unknown as Unit);
    expect(useUnitStore.getState().units).toEqual([]);
  });

  it("setUnits replaces the whole list", () => {
    useUnitStore.getState().upsertUnit(unit("a:b:c"));
    useUnitStore.getState().setUnits([unit("x:y:z")]);
    expect(useUnitStore.getState().units.map((u) => u.frame.key)).toEqual(["x:y:z"]);
  });

  it("removeUnit drops it and clears the modal when it was open on it", () => {
    useUnitStore.getState().upsertUnit(unit("a:b:c"));
    useUnitStore.getState().openDetail("a:b:c");
    useUnitStore.getState().removeUnit("a:b:c");
    expect(useUnitStore.getState().units).toEqual([]);
    expect(useUnitStore.getState().activeKey).toBeNull();
    expect(useUnitStore.getState().detailOpen).toBe(false);
  });

  it("closeDetail nulls the active key so the modal unmounts", () => {
    // Load-bearing: the modal fetches on mount, and a standing singleton key
    // (the prepared_work queue) would otherwise show decided rows as pending.
    useUnitStore.getState().openDetail("muldro:prepared_work:ws_1");
    useUnitStore.getState().closeDetail();
    expect(useUnitStore.getState().activeKey).toBeNull();
    expect(useUnitStore.getState().detailOpen).toBe(false);
  });
});

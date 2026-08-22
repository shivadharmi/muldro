/**
 * The workspace's Units, keyed on `frame.key`.
 *
 * The surface store keyed on a minted `surf_ULID`, so three polls of one email
 * thread minted three ids and rendered three cards — spec §1 defect 1.
 * `frame.key` is `source:entity_type:entity_id`, supplied by the source system
 * and stable by construction, so the second message on a thread updates the
 * card that is already there.
 *
 * There is no cap and no expiry here. The feed is a projection of live rows
 * (spec §10 invariants 1 and 9); what bounds it is the server's window and the
 * ranker's order, not a client-side slice.
 */

import { create } from "zustand";
import type { Unit } from "@/lib/types/unit";

interface UnitState {
  units: Unit[];
  activeKey: string | null;
  detailOpen: boolean;
  upsertUnit: (unit: Unit) => void;
  setUnits: (units: Unit[]) => void;
  removeUnit: (key: string) => void;
  openDetail: (key: string) => void;
  closeDetail: () => void;
}

/** TOTAL against a malformed WS frame: a bad push must cost one card, not the page. */
function keyOf(unit: Unit | null | undefined): string | null {
  const key = unit?.frame?.key;
  return typeof key === "string" && key ? key : null;
}

export const useUnitStore = create<UnitState>((set) => ({
  units: [],
  activeKey: null,
  detailOpen: false,

  upsertUnit: (unit) =>
    set((state) => {
      const key = keyOf(unit);
      if (!key) return state;
      const idx = state.units.findIndex((u) => u.frame.key === key);
      if (idx === -1) return { units: [...state.units, unit] };
      const next = [...state.units];
      next[idx] = unit;
      return { units: next };
    }),

  setUnits: (units) => set({ units: units.filter((u) => keyOf(u) !== null) }),

  removeUnit: (key) =>
    set((state) => ({
      units: state.units.filter((u) => u.frame.key !== key),
      activeKey: state.activeKey === key ? null : state.activeKey,
      detailOpen: state.activeKey === key ? false : state.detailOpen,
    })),

  openDetail: (key) => set({ activeKey: key, detailOpen: true }),

  // Nulling the key is load-bearing, not tidiness: it unmounts UnitDetail so
  // its per-open fetch runs again next time. The prepared-work queue is a
  // STANDING singleton key, so a cached open would show decided rows as
  // outstanding — the same bug closeDetailModal's comment records.
  closeDetail: () => set({ activeKey: null, detailOpen: false }),
}));

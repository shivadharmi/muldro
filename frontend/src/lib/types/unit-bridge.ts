/**
 * TEMPORARY. Bridges the old surface payload onto a Unit until the backend
 * Unit transport lands (the emitter is plan Task 12; nothing publishes Units
 * yet). Deleted together with the surface store — it is not a second, durable
 * route from data to a card, and must never grow into one.
 *
 * It lives here rather than in `unit.ts` precisely because it is temporary:
 * `unit.ts` is the durable mirror of `backend/src/view/contracts.py` and must
 * not import the store it outlives. Deleting this bridge should be `rm` plus
 * two import fixes.
 *
 * Deliberately minimal: `kind: "record"` and `status: "seen"` are flat
 * constants rather than a surface-kind mapping, because a second kind
 * taxonomy is exactly what the frame/body rebuild exists to delete. Do not
 * enrich this; enrich the backend emitter instead.
 */

import type { WorkspaceSurface } from "@/stores/surface-store";
import type { Unit } from "./unit";

// TODO(view-layer): delete with the surface store once the Unit transport lands.
export function unitFromSurface(s: WorkspaceSurface): Unit {
  // `created_at` is always present on a WorkspaceSurface; the epoch would
  // render as "20000d ago" on every card that lacks a preview timestamp.
  const occurred = s.preview.timestamp ?? s.created_at;
  return {
    frame: {
      key: s.id,
      group_key: null,
      kind: "record",
      status: "seen",
      headline: s.preview.title,
      source: s.preview.tags[0] ?? "muldro",
      entity_type: s.kind,
      occurred_at: occurred,
      updated_at: s.preview.updated_at ?? occurred,
      importance: 0,
      event_count: 1,
      affordances: [],
    },
    body: s.preview.subtitle ?? "",
    quotes: [],
  };
}

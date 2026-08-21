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

/**
 * TOTAL against a missing or partial `preview`, and it must stay that way.
 *
 * The store's `addSurface` guards only `id` and `kind`, so a malformed
 * `workspace_surface_push` carrying no `preview` reaches this function — and
 * this function runs in the *page* body, above the per-card `ErrorBoundary`.
 * A `TypeError` here does not cost one card; it blanks the whole workspace.
 *
 * The alternative fix was to map inside `WorkspaceCanvas`'s boundary, but that
 * would make the durable canvas take `WorkspaceSurface[]` — coupling it to the
 * store this bridge exists to outlive, and leaving the chat panel's call site
 * unguarded anyway. Totality here covers both call sites and keeps the delete
 * a one-file `rm`.
 *
 * Missing fields degrade to empty, never to invented copy: an empty headline
 * renders an empty line and an empty timestamp renders `--` via `TimeAgo`.
 */
// TODO(view-layer): delete with the surface store once the Unit transport lands.
export function unitFromSurface(s: WorkspaceSurface): Unit {
  const preview: Partial<WorkspaceSurface["preview"]> = s.preview ?? {};
  // `created_at` is present on every well-formed WorkspaceSurface; the epoch
  // would render as "20000d ago" on every card that lacks a preview timestamp.
  const occurred = preview.timestamp ?? s.created_at ?? "";
  return {
    frame: {
      key: s.id,
      group_key: null,
      kind: "record",
      status: "seen",
      headline: preview.title ?? "",
      source: preview.tags?.[0] ?? "muldro",
      entity_type: s.kind,
      occurred_at: occurred,
      updated_at: preview.updated_at ?? occurred,
      importance: 0,
      event_count: 1,
      affordances: [],
    },
    body: preview.subtitle ?? "",
    quotes: [],
  };
}

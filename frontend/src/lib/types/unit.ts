/**
 * Mirrors backend/src/view/contracts.py.
 *
 * Code owns the frame; the model writes `body` and nothing else; `quotes`
 * carries external text with its attribution and is the only route by which
 * external text reaches the screen.
 */

import type { WorkspaceSurface } from "@/stores/surface-store";

export type FrameKind =
  | "proposal"
  | "finding"
  | "run"
  | "record"
  | "briefing";

export type FrameStatus =
  | "needs_you"
  | "scheduled"
  | "running"
  | "done"
  | "failed"
  | "new"
  | "seen";

export interface Affordance {
  /** A capability in CAPABILITY_CATALOG. Never model-authored. */
  capability: string;
  /** Code-authored button text. */
  label: string;
  variant: "primary" | "secondary";
}

export interface Frame {
  key: string;
  group_key: string | null;
  kind: FrameKind;
  status: FrameStatus;
  /** PLAIN TEXT. Never pass this to a markdown renderer. */
  headline: string;
  source: string;
  entity_type: string;
  occurred_at: string;
  updated_at: string;
  importance: number;
  event_count: number;
  affordances: Affordance[];
}

export interface Quote {
  /** Verbatim external text. Rendered as plain text, never as markdown. */
  text: string;
  who: string;
  when: string;
}

export interface Unit {
  frame: Frame;
  /** One markdown field. The card renders paragraph 1; the Full renders all. */
  body: string;
  quotes: Quote[];
}

/**
 * TEMPORARY. Bridges the old surface payload onto a Unit until the backend
 * Unit transport lands (the emitter is plan Task 12; nothing publishes Units
 * yet). Deleted together with the surface store — it is not a second, durable
 * route from data to a card, and must never grow into one.
 *
 * Deliberately minimal: `kind: "record"` and `status: "seen"` are flat
 * constants rather than a surface-kind mapping, because a second kind
 * taxonomy is exactly what the frame/body rebuild exists to delete. Do not
 * enrich this; enrich the backend emitter instead.
 */
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

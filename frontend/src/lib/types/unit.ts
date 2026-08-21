/**
 * Mirrors backend/src/view/contracts.py.
 *
 * Code owns the frame; the model writes `body` and nothing else; `quotes`
 * carries external text with its attribution and is the only route by which
 * external text reaches the screen.
 */

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

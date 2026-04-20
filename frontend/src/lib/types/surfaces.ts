/** Surface kind taxonomy — matches backend SurfaceKind Literal. */

export type SurfaceKind =
  // System-managed (detail API exposed)
  | "run"
  | "summary"
  | "briefing"
  | "alert"
  | "recommendation"
  | "proactive_insight"
  // Agent-managed (inline children, no detail API)
  | "message"
  // Legacy kinds retained so existing persisted surfaces still render
  | "plan"
  | "checklist"
  | "approval"
  | "comparison"
  | "timeline"
  | "table"
  | "activity";

/** Surfaces for which the frontend fetches detail tabs from the API. */
export const SYSTEM_SURFACE_KINDS: ReadonlySet<SurfaceKind> = new Set([
  "run",
  "summary",
  "briefing",
  "alert",
  "recommendation",
  "proactive_insight",
]);

/** Surfaces authored by the agent — children carry the whole detail. */
export const AGENT_SURFACE_KINDS: ReadonlySet<SurfaceKind> = new Set(["message"]);

export function isSystemSurface(kind: SurfaceKind): boolean {
  return SYSTEM_SURFACE_KINDS.has(kind);
}

export function isAgentSurface(kind: SurfaceKind): boolean {
  return AGENT_SURFACE_KINDS.has(kind);
}

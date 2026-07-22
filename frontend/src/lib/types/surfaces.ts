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
  | "approval";

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

/** Every recognized surface kind — used to detect backend↔frontend contract drift. */
export const ALL_SURFACE_KINDS: ReadonlySet<string> = new Set([
  ...SYSTEM_SURFACE_KINDS,
  ...AGENT_SURFACE_KINDS,
  "plan",
  "approval",
]);

/**
 * Coerce a raw `kind` from the API into a known SurfaceKind, defaulting to
 * "summary". A *non-empty* unrecognized kind signals contract drift (the backend
 * shipped a kind the frontend doesn't know) and is surfaced as a warning while
 * still degrading gracefully. A missing/empty kind is treated as the normal
 * default without noise.
 */
export function normalizeSurfaceKind(raw: string | null | undefined, surfaceId: string): SurfaceKind {
  if (!raw) return "summary";
  if (!ALL_SURFACE_KINDS.has(raw)) {
    console.warn(
      `[surfaces] Unknown surface kind "${raw}" for surface ${surfaceId}; falling back to "summary". ` +
        "Backend/frontend SurfaceKind taxonomies may have drifted.",
    );
    return "summary";
  }
  return raw as SurfaceKind;
}

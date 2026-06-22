/** Shared surface ordering used by the workspace grid and the chat surfaces panel. */

import type { WorkspaceSurface } from "@/stores/surface-store";

/**
 * Surfaces that should float to the top of a list: live execution phases plus
 * proactive insights (which need attention). Kept in one place so the workspace
 * grid and the chat panel agree on what "active" means.
 */
function isActiveSurface(s: WorkspaceSurface): boolean {
  return (
    s.phase === "executing" ||
    s.phase === "approval_needed" ||
    s.phase === "planning" ||
    s.kind === "proactive_insight"
  );
}

/**
 * Order surfaces active-first, then newest-first, with a stable id tie-break so the
 * order is deterministic when two surfaces share a created_at. Returns a new array;
 * never mutates the input.
 */
export function sortSurfacesActiveFirst(
  surfaces: readonly WorkspaceSurface[],
): WorkspaceSurface[] {
  return [...surfaces].sort((a, b) => {
    const aActive = isActiveSurface(a) ? 0 : 1;
    const bActive = isActiveSurface(b) ? 0 : 1;
    if (aActive !== bActive) return aActive - bActive;
    const byDate = b.created_at.localeCompare(a.created_at);
    return byDate !== 0 ? byDate : a.id.localeCompare(b.id);
  });
}

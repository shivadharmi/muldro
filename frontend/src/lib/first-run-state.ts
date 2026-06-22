export type FirstRunState = "onboarding" | "gathering" | "active";

/**
 * Decide which first-load state the workspace should render. Precedence:
 * an existing briefing always wins (an active user who disconnects all
 * sources is not thrown back to onboarding), then zero sources is the
 * first-run onboarding state, otherwise we are warming up.
 */
export function resolveFirstRunState(
  sourceCount: number,
  hasBriefing: boolean,
): FirstRunState {
  if (hasBriefing) return "active";
  if (sourceCount === 0) return "onboarding";
  return "gathering";
}

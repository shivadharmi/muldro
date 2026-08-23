export type FirstRunState = "onboarding" | "gathering" | "active";

/**
 * Decide which first-load state the workspace should render. Precedence:
 * an existing briefing always wins (an active user who disconnects all
 * sources is not thrown back to onboarding), then zero sources is the
 * first-run onboarding state, then anything at all to look at means the
 * workspace is live, and only a genuinely empty one is warming up.
 *
 * `hasUnits` is what stops an EMPTY STATE RENDERING OVER CONTENT. This asked
 * only about the briefing, so a workspace with five findings on screen still
 * announced "gathering data for your first briefing" above them — the briefing
 * runs on a daily schedule, so that card sat there all day while muldro was
 * visibly working underneath it. An empty state is a claim that there is
 * nothing to see, and it must be false the moment there is.
 */
export function resolveFirstRunState(
  sourceCount: number,
  hasBriefing: boolean,
  hasUnits: boolean = false,
): FirstRunState {
  if (hasBriefing) return "active";
  if (sourceCount === 0) return "onboarding";
  if (hasUnits) return "active";
  return "gathering";
}

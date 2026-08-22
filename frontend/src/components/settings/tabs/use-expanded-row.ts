"use client";

import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { useSettingsModalStore } from "@/stores/settings-modal-store";

/** The one open row of the Providers tab, and — when it was opened FOR the
 *  founder rather than BY them — why. One piece of state and not two: a reason
 *  that outlived its row would chip whichever row opened next. */
export interface ExpandedRow {
  provider: string;
  reason?: string;
}

/**
 * Which provider row is open, seeded from the cross-tab intent.
 *
 * The Model tab's `Connect {provider}` sends the founder to Providers FOR
 * something, and the reason rides in the store because the two tabs share no
 * parent that could hold it. The intent is one-shot: it is read as this tab's
 * initial state and acknowledged on mount, so coming back later must not re-open
 * a row already dealt with — or one since connected.
 *
 * **Read during render, cleared in an effect.** The read is a bare `getState()`
 * inside a lazy initialiser: pure, so StrictMode's double invocation and any
 * discarded render both produce the same answer, and it deliberately does not
 * SUBSCRIBE — a subscriber would re-render on the clear that immediately
 * follows. The clear is a write to an external system, which is what an effect
 * is for; doing it as `setExpanded` in an effect body instead would be the
 * cascading-render pattern the lint rule forbids, and would render the tab once
 * with the wrong row open.
 *
 * Exclusivity is not enforced here because there is nothing to enforce: the
 * single slot IS the rule, so a pre-expand travels the same path as a click
 * rather than a parallel one.
 */
export function useExpandedRow(): [
  ExpandedRow | null,
  Dispatch<SetStateAction<ExpandedRow | null>>,
] {
  const [expanded, setExpanded] = useState<ExpandedRow | null>(
    () => useSettingsModalStore.getState().pendingProvider,
  );
  const clearPendingProvider = useSettingsModalStore(
    (s) => s.clearPendingProvider,
  );

  useEffect(() => {
    clearPendingProvider();
  }, [clearPendingProvider]);

  return [expanded, setExpanded];
}

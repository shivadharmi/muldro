"use client";

import { useCallback, useEffect, useState } from "react";

import { useSettingsModalStore } from "@/stores/settings-modal-store";

/** The one open row of the Providers tab, and — when it was opened FOR the
 *  founder rather than BY them — why. One piece of state and not two: a reason
 *  that outlived its row would chip whichever row opened next. */
export interface ExpandedRow {
  provider: string;
  reason?: string;
}

export interface ExpandedRowControls {
  /** The open row, or `null` when every row is collapsed. */
  expanded: ExpandedRow | null;
  /**
   * The provider a cross-tab intent opened on THIS mount, or `null` for an
   * ordinary visit. Stable for the component's lifetime, so an effect keyed on
   * it runs once.
   *
   * A separate signal rather than `expanded?.reason`, because the two answer
   * different questions. `expanded` is live state the founder can toggle away a
   * keystroke later; this is a fact about how the mount BEGAN, and it is what
   * moving focus must depend on. It is also non-null for a reasonless intent,
   * which the derived form would miss.
   */
  arrivedAt: string | null;
  /** Open a row, exclusively. Deliberately cannot carry a reason: a reason
   *  exists only because an intent created it, so a caller must not be able to
   *  write one by hand. */
  open: (provider: string) => void;
  /** Collapse whatever is open. */
  close: () => void;
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
 * **This is a mount-time hook with a side effect**, which is why it returns a
 * named object rather than a `useState`-shaped tuple: the tuple read as ordinary
 * local state and said nothing about the store write, and its setter was wide
 * enough to fabricate a `reason` from anywhere.
 *
 * **Read during render, cleared in an effect.** The read is a bare `getState()`
 * inside a lazy initialiser: pure, so StrictMode's double invocation and any
 * discarded render both produce the same answer — a render thrown away before
 * commit leaves the intent intact for the retry, which a consume-and-clear read
 * would have eaten. It deliberately does not SUBSCRIBE, so the clear that
 * follows re-renders nothing. The clear is a write to an external system, which
 * is what an effect is for; doing it as `setState` in an effect body instead
 * would be the cascading-render pattern the lint rule forbids, and would render
 * the tab once with the wrong row open.
 *
 * An intent naming a provider with **no row** — one absent from both the catalog
 * and this workspace's credentials — is a silent no-op: nothing expands and
 * nothing is said. There is no fallback and no message, because there is no such
 * provider today (`model_config_service` lists every catalogued provider plus
 * every stray). If one ever becomes reachable, it needs a message here.
 *
 * Exclusivity is not enforced here because there is nothing to enforce: the
 * single slot IS the rule, so a pre-expand travels the same path as a click
 * rather than a parallel one.
 */
export function useExpandedRow(): ExpandedRowControls {
  // Read ONCE, purely, and kept: `arrivedAt` must not be re-derived from state
  // the founder can change.
  const [intent] = useState(
    () => useSettingsModalStore.getState().pendingProvider,
  );
  const [expanded, setExpanded] = useState<ExpandedRow | null>(intent);
  const clearPendingProvider = useSettingsModalStore(
    (s) => s.clearPendingProvider,
  );

  useEffect(() => {
    clearPendingProvider();
  }, [clearPendingProvider]);

  const open = useCallback(
    (provider: string) => setExpanded({ provider }),
    [],
  );
  const close = useCallback(() => setExpanded(null), []);

  return { expanded, arrivedAt: intent?.provider ?? null, open, close };
}

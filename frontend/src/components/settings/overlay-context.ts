"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

interface SettingsOverlayValue {
  /**
   * Take the keyboard for a NESTED overlay rendered over the dialog panel, such
   * as the model picker. NOTE: the picker is NOT portalled — it renders in place
   * inside the shell's `overflow-hidden` panel, escaping it only because
   * `position: fixed` resolves against the viewport. That holds today solely
   * because the panel's one transform (`animate-scale-in`) has no `forwards`
   * fill and so leaves nothing behind; a `will-change`, `filter`,
   * `backdrop-filter` or persistent `transform` on any ancestor would make the
   * fixed wrapper resolve against THAT ancestor and clip the palette to the
   * panel, with no test failing. Portal it if that day comes.
   * The shell suspends its focus trap while
   * any claim is outstanding, so the overlay's own Tab handling wins instead of
   * being fought by a document-scoped trap that would pull focus back into the
   * rail on the first keypress. Returns the release, which is idempotent.
   *
   * A claim is a LEASE, not a flag. A raw `setPaused(true)` left the trap
   * disarmed for the rest of the dialog's life the moment an overlay went away
   * without running its own close path — click the rail while the picker is
   * open and `TabBody` swaps it out unmounted, no close handler, Tab escaping
   * the dialog for the rest of the session. Counting leases also gets two
   * simultaneous overlays right, where a boolean unpauses on the first close.
   *
   * Pair it with `event.preventDefault()` on the overlay's own Escape handler:
   * the shell's Esc-to-close ignores an already-handled key, which is how an
   * overlay closes itself without closing the whole dialog.
   */
  claim: () => () => void;
}

/** A no-op default, so a tab rendered outside the shell still works. */
const SettingsOverlayContext = createContext<SettingsOverlayValue>({
  claim: () => () => {},
});

export const SettingsOverlayProvider = SettingsOverlayContext.Provider;

/**
 * The shell side of the lease. `claimed` drives the trap's `paused`; `value`
 * goes on the provider.
 */
export function useOverlayClaims(): {
  claimed: boolean;
  value: SettingsOverlayValue;
} {
  const [claims, setClaims] = useState(0);

  const value = useMemo<SettingsOverlayValue>(
    () => ({
      claim: () => {
        setClaims((n) => n + 1);
        let released = false;
        return () => {
          // Idempotent: a double release must not decrement someone else's
          // claim and re-arm the trap under an overlay that is still open.
          if (released) return;
          released = true;
          setClaims((n) => Math.max(0, n - 1));
        };
      },
    }),
    [],
  );

  return { claimed: claims > 0, value };
}

/**
 * The overlay side of the lease: hold it while `open`, release on close OR on
 * unmount — whichever comes first, which is the whole point.
 *
 * No consumer yet; the model picker is the first, in a later change. Kept as a
 * documented seam rather than deferred, because the shape of the trap's escape
 * hatch is what the picker has to be built against. ESLint will not flag it,
 * so this note is the flag.
 */
export function useOverlayClaim(open: boolean): void {
  const { claim } = useContext(SettingsOverlayContext);
  useEffect(() => {
    if (!open) return;
    return claim();
  }, [open, claim]);
}

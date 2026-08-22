"use client";

import { createContext, useContext } from "react";

interface SettingsOverlayValue {
  /**
   * Declare that a NESTED overlay — one portalled out of the dialog panel, such
   * as the model picker — has taken the keyboard. The shell suspends its focus
   * trap while this is true, so the overlay's own Tab handling wins instead of
   * being fought by a document-scoped trap that would pull focus back into the
   * rail on the first keypress.
   *
   * Pair it with `event.preventDefault()` on the overlay's own Escape handler:
   * the shell's Esc-to-close ignores an already-handled key, which is how an
   * overlay closes itself without closing the whole dialog.
   */
  setOverlayOpen: (open: boolean) => void;
}

/** A no-op default, so a tab rendered outside the shell still works. */
const SettingsOverlayContext = createContext<SettingsOverlayValue>({
  setOverlayOpen: () => {},
});

export const SettingsOverlayProvider = SettingsOverlayContext.Provider;

export function useSettingsOverlay(): SettingsOverlayValue {
  return useContext(SettingsOverlayContext);
}

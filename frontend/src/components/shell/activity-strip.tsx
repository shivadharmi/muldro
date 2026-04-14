"use client";

/**
 * ActivityStrip is now minimal — the primary activity indicator
 * has moved to TopBar for better visibility. This strip only shows
 * when there are multiple recent events worth surfacing.
 */
export function ActivityStrip() {
  // Activity indicator is now integrated into TopBar.
  // Keep this component as a no-op to avoid breaking the layout.
  return null;
}

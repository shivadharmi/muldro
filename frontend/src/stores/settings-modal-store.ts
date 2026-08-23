/** Controls the Settings popup modal: open state, active tab, and the one
 *  cross-tab intent — "open Providers *for this provider*". */

import { create } from "zustand";

export type SettingsTab =
  | "account"
  | "preferences"
  | "policy"
  | "budget"
  | "trust"
  | "filters"
  | "model"
  | "providers";

/**
 * Why the Providers tab is about to open, when it was opened FOR something.
 *
 * A slug, not a display name: the Providers tab already resolves names from the
 * catalog, and a name computed on the Model tab would be a second answer to
 * that question. The `reason` is a whole sentence rather than a tier slug —
 * the Providers tab knows nothing about tiers, and rendering the chip must not
 * require it to learn.
 */
export interface PendingProvider {
  provider: string;
  reason?: string;
}

interface SettingsModalState {
  open: boolean;
  activeTab: SettingsTab;
  /**
   * A one-shot intent, NOT a selection. It lives only between the navigation
   * that created it and the Providers tab mount that consumes it, which is why
   * every other navigation action below drops it: an intent that outlived its
   * trip would re-open a row the founder has already dealt with.
   */
  pendingProvider: PendingProvider | null;
  openSettings: (tab?: SettingsTab) => void;
  closeSettings: () => void;
  setActiveTab: (tab: SettingsTab) => void;
  /**
   * Switch to the Providers tab WITH a provider to expand and a reason to show
   * on it. Every fact is set in one write, so the tab can never render before
   * the intent that sent the founder there.
   *
   * It OPENS the modal as well as switching tabs, even though today's only
   * caller is already inside it. The alternative was a docstring saying "for
   * within-modal use only", and the first caller from outside — a workspace-level
   * "connect a provider" prompt — would have reached for `openSettings` first,
   * which CLEARS the intent it had just set, silently and with nothing to read.
   */
  openProviderFor: (provider: string, reason?: string) => void;
  /**
   * The other half of consuming one: acknowledge the intent so it cannot be
   * read twice.
   *
   * Split from the read rather than offered as one consume-and-clear call,
   * because the two halves belong in different phases. The reader needs the
   * value while it is deciding what to render — during render, where a store
   * write is a side effect and StrictMode's double invocation would hand the
   * second caller `null`. Clearing is a write to an external system, which is
   * what an effect is FOR, and is the one place it can happen without a
   * cascading re-render. A combined call would have to be made from one of the
   * two, and either choice is wrong for the other half.
   */
  clearPendingProvider: () => void;
}

export const useSettingsModalStore = create<SettingsModalState>((set, get) => ({
  open: false,
  activeTab: "account",
  pendingProvider: null,
  openSettings: (tab) =>
    set({ open: true, activeTab: tab ?? "account", pendingProvider: null }),
  closeSettings: () => set({ open: false, pendingProvider: null }),
  setActiveTab: (tab) => set({ activeTab: tab, pendingProvider: null }),
  openProviderFor: (provider, reason) =>
    set({
      open: true,
      activeTab: "providers",
      pendingProvider: { provider, reason },
    }),
  clearPendingProvider: () => {
    // Guarded so a tab mounting with no intent does not notify every subscriber
    // of a null→null "change".
    if (get().pendingProvider) set({ pendingProvider: null });
  },
}));

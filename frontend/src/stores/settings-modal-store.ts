/** Controls the Settings popup modal: open state + active tab. */

import { create } from "zustand";

export type SettingsTab = "account" | "preferences" | "policy" | "budget" | "trust" | "model";

interface SettingsModalState {
  open: boolean;
  activeTab: SettingsTab;
  openSettings: (tab?: SettingsTab) => void;
  closeSettings: () => void;
  setActiveTab: (tab: SettingsTab) => void;
}

export const useSettingsModalStore = create<SettingsModalState>((set) => ({
  open: false,
  activeTab: "account",
  openSettings: (tab) => set({ open: true, activeTab: tab ?? "account" }),
  closeSettings: () => set({ open: false }),
  setActiveTab: (tab) => set({ activeTab: tab }),
}));

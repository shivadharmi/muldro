/** Shell-level state: right sidebar and the command launcher. */

import { create } from "zustand";

interface ShellState {
  // Right sidebar
  rightSidebarOpen: boolean;
  rightSidebarTab: "context" | "evidence" | "activity";
  toggleRightSidebar: () => void;
  setRightSidebarTab: (tab: "context" | "evidence" | "activity") => void;

  // Command launcher
  commandLauncherOpen: boolean;
  toggleCommandLauncher: () => void;
  closeCommandLauncher: () => void;
}

export const useShellStore = create<ShellState>((set) => ({
  rightSidebarOpen: false,
  rightSidebarTab: "context",
  toggleRightSidebar: () =>
    set((s) => ({ rightSidebarOpen: !s.rightSidebarOpen })),
  setRightSidebarTab: (tab) => set({ rightSidebarTab: tab }),

  commandLauncherOpen: false,
  toggleCommandLauncher: () =>
    set((s) => ({ commandLauncherOpen: !s.commandLauncherOpen })),
  closeCommandLauncher: () => set({ commandLauncherOpen: false }),
}));

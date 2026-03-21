/** Shell-level state: topbar, workspace, sidebar, command launcher, mobile. */

import { create } from "zustand";

interface ShellState {
  // Workspace
  workspaceId: string | null;
  setWorkspaceId: (id: string | null) => void;

  // Right sidebar
  rightSidebarOpen: boolean;
  rightSidebarTab: "context" | "evidence" | "activity";
  toggleRightSidebar: () => void;
  setRightSidebarTab: (tab: "context" | "evidence" | "activity") => void;

  // Command launcher
  commandLauncherOpen: boolean;
  toggleCommandLauncher: () => void;
  closeCommandLauncher: () => void;

  // Mobile
  isMobileNavOpen: boolean;
  toggleMobileNav: () => void;
}

export const useShellStore = create<ShellState>((set) => ({
  workspaceId: null,
  setWorkspaceId: (id) => set({ workspaceId: id }),

  rightSidebarOpen: false,
  rightSidebarTab: "context",
  toggleRightSidebar: () =>
    set((s) => ({ rightSidebarOpen: !s.rightSidebarOpen })),
  setRightSidebarTab: (tab) => set({ rightSidebarTab: tab }),

  commandLauncherOpen: false,
  toggleCommandLauncher: () =>
    set((s) => ({ commandLauncherOpen: !s.commandLauncherOpen })),
  closeCommandLauncher: () => set({ commandLauncherOpen: false }),

  isMobileNavOpen: false,
  toggleMobileNav: () =>
    set((s) => ({ isMobileNavOpen: !s.isMobileNavOpen })),
}));

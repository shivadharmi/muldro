"use client";

import { useShellStore } from "@/stores/shell-store";

export function TopBar() {
  const { toggleCommandLauncher, toggleRightSidebar } = useShellStore();

  return (
    <div className="h-12 border-b border-b-primary bg-surface-0 flex items-center px-4 gap-3">
      {/* Global command input */}
      <button
        onClick={toggleCommandLauncher}
        className="flex-1 max-w-xl h-8 px-3 rounded-[var(--radius-sm)] bg-surface-1 border border-b-primary text-t-tertiary text-sm text-left hover:border-accent-primary transition-colors cursor-pointer"
      >
        <span className="opacity-60">Ask Jarvis anything...</span>
        <kbd className="ml-auto text-xs opacity-40 hidden sm:inline">⌘K</kbd>
      </button>

      {/* Right side controls */}
      <div className="ml-auto flex items-center gap-2">
        {/* Activity / sidebar toggle */}
        <button
          onClick={toggleRightSidebar}
          className="p-2 rounded-[var(--radius-sm)] text-t-secondary hover:text-t-primary hover:bg-surface-1 transition-colors cursor-pointer"
          aria-label="Toggle context sidebar"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <rect
              x="1" y="1" width="16" height="16" rx="2"
              stroke="currentColor" strokeWidth="1.5"
            />
            <line
              x1="12" y1="1" x2="12" y2="17"
              stroke="currentColor" strokeWidth="1.5"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}

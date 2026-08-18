"use client";

import { useShellStore } from "@/stores/shell-store";
import { useActivityStore } from "@/stores/activity-store";

export function TopBar() {
  const { toggleCommandLauncher, toggleRightSidebar, rightSidebarOpen } = useShellStore();
  const { events, unreadCount, markAllRead } = useActivityStore();
  const latest = events[0];

  const runningTool = latest?.payload?.tool_name
    ? String(latest.payload.tool_name)
    : null;

  return (
    <div className="h-12 border-b border-b-secondary bg-surface-0/80 backdrop-blur-md flex items-center px-4 gap-3">
      {/* Global command input — spotlight style */}
      <button
        onClick={toggleCommandLauncher}
        className="flex items-center gap-2 flex-1 max-w-lg h-8 px-3 rounded-[var(--radius-md)] bg-surface-1 border border-b-secondary text-t-muted text-[13px] text-left hover:border-j-primary/40 hover:bg-surface-2 transition-all duration-150 cursor-pointer"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="text-t-muted shrink-0">
          <circle cx="7" cy="7" r="4" stroke="currentColor" strokeWidth="1.4" />
          <path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <span className="flex-1 truncate">Ask Muldro anything…</span>
        <kbd className="text-[10px] text-t-muted font-mono bg-surface-2 px-1.5 py-0.5 rounded hidden sm:inline">
          ⌘K
        </kbd>
      </button>

      {/* Running-tool indicator — tool name in mono + queue-count pill */}
      {runningTool ? (
        <div className="hidden md:flex items-center gap-2 max-w-[260px]">
          <span className="font-mono text-[11px] text-j-primary truncate">{runningTool}</span>
          {unreadCount > 0 && (
            <button
              onClick={markAllRead}
              className="shrink-0 inline-flex items-center justify-center min-w-5 h-5 px-1 rounded-full bg-j-primary text-j-primary-fg text-[10px] font-semibold hover:bg-j-primary-hover transition-colors cursor-pointer"
              title="Activity in queue — mark all read"
            >
              {unreadCount > 99 ? "99" : unreadCount}
            </button>
          )}
        </div>
      ) : (
        latest && (
          <div className="hidden md:flex items-center gap-2 text-[11px] text-t-muted max-w-[240px]">
            {unreadCount > 0 && (
              <button
                onClick={markAllRead}
                className="shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full bg-j-primary text-j-primary-fg text-[10px] font-semibold hover:bg-j-primary-hover transition-colors cursor-pointer"
                title="Mark all read"
              >
                {unreadCount > 99 ? "99" : unreadCount}
              </button>
            )}
            <span className="truncate">{latest.event_type.replace(/_/g, " ")}</span>
          </div>
        )
      )}

      {/* Right side controls */}
      <div className="ml-auto flex items-center gap-2">
        {/* Live indicator — pulsing green dot */}
        <div className="hidden sm:flex items-center gap-1.5 text-[11px] text-t-muted">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-j-success opacity-70 animate-pulse-live" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-j-success" />
          </span>
          <span className="uppercase tracking-wide text-[10px]">live</span>
        </div>
        <button
          onClick={toggleRightSidebar}
          className={`p-2 rounded-[var(--radius-md)] transition-colors cursor-pointer ${
            rightSidebarOpen
              ? "text-j-primary bg-j-primary-soft"
              : "text-t-muted hover:text-t-primary hover:bg-surface-2"
          }`}
          aria-label="Toggle context sidebar"
        >
          <svg width="16" height="16" viewBox="0 0 18 18" fill="none">
            <rect
              x="1.5" y="1.5" width="15" height="15" rx="2.5"
              stroke="currentColor" strokeWidth="1.4"
            />
            <line
              x1="12" y1="1.5" x2="12" y2="16.5"
              stroke="currentColor" strokeWidth="1.4"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}

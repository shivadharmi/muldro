"use client";

import { useActivityStore } from "@/stores/activity-store";

export function ActivityStrip() {
  const { events, unreadCount, markAllRead } = useActivityStore();

  if (events.length === 0) return null;

  const latest = events[0];

  return (
    <div className="h-8 bg-surface-1 border-b border-b-primary flex items-center px-4 text-xs text-t-tertiary gap-2">
      {unreadCount > 0 && (
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-accent-primary text-white text-[10px] font-medium">
          {unreadCount > 99 ? "99+" : unreadCount}
        </span>
      )}
      <span className="truncate flex-1">
        {latest.event_type.replace(/_/g, " ")}
        {latest.payload?.tool_name
          ? `: ${String(latest.payload.tool_name)}`
          : ""}
      </span>
      {unreadCount > 0 && (
        <button
          onClick={markAllRead}
          className="text-accent-primary hover:underline cursor-pointer"
        >
          Mark read
        </button>
      )}
    </div>
  );
}

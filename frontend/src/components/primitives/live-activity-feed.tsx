"use client";

import { useEffect } from "react";
import { useActivityStore } from "@/stores/activity-store";

export function LiveActivityFeed() {
  const { events, initialize } = useActivityStore();

  useEffect(() => {
    initialize();
  }, [initialize]);

  if (events.length === 0) {
    return (
      <div className="text-sm text-t-tertiary">No recent activity.</div>
    );
  }

  return (
    <ul className="space-y-2">
      {events.slice(0, 50).map((event) => (
        <li
          key={event.event_id}
          className="flex items-start gap-2 text-sm"
        >
          <EventDot type={event.event_type} />
          <div className="flex-1 min-w-0">
            <p className="text-t-secondary truncate">
              {formatEventType(event.event_type)}
              {typeof event.payload?.tool_name === "string" && (
                <span className="text-t-tertiary">
                  {" — "}
                  {event.payload.tool_name}
                </span>
              )}
            </p>
            <p className="text-xs text-t-tertiary">
              {formatTime(event.occurred_at)}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function EventDot({ type }: { type: string }) {
  let color = "bg-surface-2";
  if (type === "run_completed") color = "bg-status-success";
  if (type === "run_failed") color = "bg-status-error";
  if (type === "approval_requested") color = "bg-status-warning";
  if (type.includes("tool_call")) color = "bg-accent-primary";

  return <span className={`w-2 h-2 rounded-full mt-1.5 ${color}`} />;
}

function formatEventType(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
}

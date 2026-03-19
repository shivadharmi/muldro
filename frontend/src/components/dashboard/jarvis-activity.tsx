"use client";

import type { DashboardTrace, DashboardEvent } from "@/lib/types";

function sourceIcon(source: string) {
  switch (source) {
    case "gmail":
      return "📧";
    case "google_calendar":
      return "📅";
    case "github":
      return "🔧";
    case "slack":
      return "💬";
    case "chat":
      return "🤖";
    case "trigger":
      return "⚡";
    case "schedule":
      return "🕐";
    default:
      return "📌";
  }
}

function timeAgo(ts: string | null): string {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function traceDescription(t: DashboardTrace): string {
  const agents = t.agents_invoked.join(" → ");
  const time = t.duration_ms ? `${(t.duration_ms / 1000).toFixed(1)}s` : "";
  return `${agents}${time ? ` (${time})` : ""}`;
}

export function JarvisActivity({
  traces,
  events,
}: {
  traces: DashboardTrace[];
  events: DashboardEvent[];
}) {
  // Merge and interleave traces and events by time
  const items: Array<{
    key: string;
    icon: string;
    description: string;
    detail: string;
    time: string;
    type: "trace" | "event";
  }> = [];

  for (const t of traces) {
    items.push({
      key: t.trace_id,
      icon: sourceIcon(t.trigger),
      description: traceDescription(t),
      detail: `$${t.total_cost_usd.toFixed(3)}`,
      time: "",
      type: "trace",
    });
  }

  for (const e of events) {
    items.push({
      key: `${e.source}-${e.occurred_at}`,
      icon: sourceIcon(e.source),
      description: e.title || `${e.event_type} from ${e.source}`,
      detail: e.source,
      time: timeAgo(e.occurred_at),
      type: "event",
    });
  }

  // Show max 8
  const display = items.slice(0, 8);

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-5">
      <h3 className="text-sm font-semibold text-t-primary mb-3">What Jarvis did</h3>
      {display.length === 0 ? (
        <p className="text-xs text-t-muted">No recent activity.</p>
      ) : (
        <div className="space-y-2">
          {display.map((item) => (
            <div key={item.key} className="flex items-start gap-2.5">
              <span className="text-sm shrink-0 mt-0.5">{item.icon}</span>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-t-primary truncate">{item.description}</p>
                <p className="text-[10px] text-t-muted">
                  {item.detail}
                  {item.time && ` · ${item.time}`}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

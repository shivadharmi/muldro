"use client";

import Link from "next/link";
import type { DashboardApproval } from "@/lib/types";
import type { Notification } from "@/lib/types";

interface InboxItem {
  id: string;
  title: string;
  source: string;
  urgency: "critical" | "high" | "medium" | "low";
  href: string;
  timestamp: string | null;
}

function urgencyColor(u: string) {
  switch (u) {
    case "critical":
      return "border-l-j-error";
    case "high":
      return "border-l-j-warning";
    case "medium":
      return "border-l-j-primary";
    default:
      return "border-l-b-secondary";
  }
}

function urgencyBadge(u: string) {
  switch (u) {
    case "critical":
      return "bg-j-error-soft text-j-error";
    case "high":
      return "bg-j-warning-soft text-j-warning";
    case "medium":
      return "bg-j-primary-soft text-j-primary";
    default:
      return "bg-surface-3 text-t-tertiary";
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

export function PriorityInbox({
  approvals,
  notifications,
}: {
  approvals: DashboardApproval[];
  notifications: Notification[];
}) {
  const items: InboxItem[] = [];

  for (const a of approvals) {
    items.push({
      id: a.approval_id,
      title: a.title,
      source: "Approval",
      urgency: a.risk_level === "high" ? "critical" : a.risk_level === "medium" ? "high" : "medium",
      href: `/approvals`,
      timestamp: a.created_at,
    });
  }

  const unread = notifications.filter(
    (n) => n.status === "sent" || n.status === "pending"
  );
  for (const n of unread.slice(0, 4)) {
    items.push({
      id: n.notification_id,
      title: n.title,
      source: "Notification",
      urgency: n.priority_score > 0.8 ? "high" : n.priority_score > 0.5 ? "medium" : "low",
      href: "/notifications",
      timestamp: n.created_at,
    });
  }

  items.sort((a, b) => {
    const order = { critical: 0, high: 1, medium: 2, low: 3 };
    return (order[a.urgency] ?? 3) - (order[b.urgency] ?? 3);
  });

  const display = items.slice(0, 6);

  if (display.length === 0) {
    return (
      <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-5">
        <h3 className="text-sm font-semibold text-t-primary mb-3">Priority Inbox</h3>
        <p className="text-xs text-t-muted">Nothing needs your attention right now.</p>
      </div>
    );
  }

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-t-primary">What needs my decision</h3>
        <Link href="/approvals" className="text-[11px] text-j-primary hover:underline">
          View all
        </Link>
      </div>
      <div className="space-y-1.5">
        {display.map((item) => (
          <Link
            key={item.id}
            href={item.href}
            className={`flex items-center gap-3 px-3 py-2 rounded-[var(--radius-sm)] border-l-3 hover:bg-surface-2 transition-colors ${urgencyColor(item.urgency)}`}
          >
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-t-primary truncate">{item.title}</p>
            </div>
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${urgencyBadge(item.urgency)}`}>
              {item.source}
            </span>
            <span className="text-[10px] text-t-muted shrink-0">
              {timeAgo(item.timestamp)}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

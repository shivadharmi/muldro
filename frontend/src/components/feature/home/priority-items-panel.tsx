"use client";

import Link from "next/link";

interface PriorityItem {
  item_type: string;
  item_id: string;
  title: string;
  priority: string;
  created_at: string | null;
  action_url: string;
}

interface Props {
  items: PriorityItem[];
}

const priorityColors: Record<string, string> = {
  high: "bg-status-error",
  medium: "bg-status-warning",
  low: "bg-surface-2",
  critical: "bg-status-error",
};

export function PriorityItemsPanel({ items }: Props) {
  if (items.length === 0) {
    return (
      <div className="text-sm text-t-tertiary py-4">
        No priority items. You&apos;re all caught up.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
        Priority Items
      </h3>
      <ul className="space-y-1.5">
        {items.map((item) => (
          <li key={item.item_id}>
            <Link
              href={item.action_url}
              className="flex items-center gap-3 p-2 rounded-[var(--radius-sm)] hover:bg-surface-1 transition-colors"
            >
              <span
                className={`w-2 h-2 rounded-full ${priorityColors[item.priority] ?? "bg-surface-2"}`}
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-t-primary truncate">{item.title}</p>
                <p className="text-xs text-t-tertiary capitalize">
                  {item.item_type.replace("_", " ")}
                </p>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

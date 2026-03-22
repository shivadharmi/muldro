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

const priorityStyles: Record<string, { dot: string; label: string }> = {
  critical: { dot: "bg-red-400", label: "Critical" },
  high: { dot: "bg-orange-400", label: "High" },
  medium: { dot: "bg-yellow-400", label: "Medium" },
  low: { dot: "bg-neutral-400", label: "Low" },
};

export function PriorityItemsPanel({ items }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Priority Items
        </h3>
        {items.length > 0 && (
          <span className="text-[10px] text-t-tertiary">{items.length} item{items.length > 1 ? "s" : ""}</span>
        )}
      </div>

      {items.length === 0 ? (
        <div className="flex items-center gap-3 py-3">
          <div className="w-8 h-8 rounded-full bg-green-500/10 flex items-center justify-center shrink-0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" className="text-green-400">
              <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div>
            <p className="text-xs text-t-secondary">All caught up</p>
            <p className="text-[10px] text-t-tertiary">No items needing your attention right now</p>
          </div>
        </div>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => {
            const style = priorityStyles[item.priority] || priorityStyles.medium;
            return (
              <li key={item.item_id}>
                <Link
                  href={item.action_url}
                  className="flex items-center gap-3 p-2.5 rounded-lg hover:bg-surface-1 transition-colors"
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-t-primary truncate">{item.title}</p>
                    <p className="text-[10px] text-t-tertiary capitalize">
                      {item.item_type.replace(/_/g, " ")}
                    </p>
                  </div>
                  <span className={`text-[10px] shrink-0 ${
                    item.priority === "critical" || item.priority === "high"
                      ? "text-orange-400" : "text-t-tertiary"
                  }`}>
                    {style.label}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

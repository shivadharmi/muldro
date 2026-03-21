"use client";

import Link from "next/link";
import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";

interface IntelligenceItem {
  item_type: string;
  item_id: string;
  title: string;
  summary: string;
  created_at: string | null;
}

interface Props {
  items: IntelligenceItem[];
}

export function RecentIntelligenceFeed({ items }: Props) {
  if (items.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
        Recent Intelligence
      </h3>
      <ul className="space-y-2">
        {items.map((item) => (
          <li
            key={item.item_id}
            className="p-3 rounded-[var(--radius-md)] bg-surface-0 border border-b-primary"
          >
            <Link
              href={`/briefings/${item.item_id}`}
              className="block hover:opacity-80 transition-opacity"
            >
              <p className="text-sm font-medium text-t-primary">
                {item.title}
              </p>
              {item.summary && (
                <div className="text-sm text-t-secondary mt-0.5 line-clamp-2">
                  <InlineMarkdown content={item.summary} />
                </div>
              )}
              <p className="text-xs text-t-tertiary mt-1 capitalize">
                {item.item_type}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

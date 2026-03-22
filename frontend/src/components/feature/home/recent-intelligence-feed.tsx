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
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Recent Intelligence
        </h3>
        {items.length > 0 && (
          <Link href="/briefings" className="text-[10px] text-accent-primary hover:underline">
            All briefings
          </Link>
        )}
      </div>

      {items.length === 0 ? (
        <div className="flex items-center gap-3 py-3">
          <div className="w-8 h-8 rounded-full bg-surface-2 flex items-center justify-center shrink-0">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-t-tertiary">
              <path d="M4 3h8a1 1 0 011 1v8a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M6 6h4M6 8.5h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
            </svg>
          </div>
          <div>
            <p className="text-xs text-t-secondary">No briefings yet</p>
            <p className="text-[10px] text-t-tertiary">Intelligence appears as Jarvis processes data</p>
          </div>
        </div>
      ) : (
        <div className="space-y-2 max-h-[280px] overflow-y-auto -mx-1 px-1">
          {items.map((item) => (
            <Link
              key={item.item_id}
              href={`/briefings/${item.item_id}`}
              className="block p-2.5 rounded-lg hover:bg-surface-1 transition-colors"
            >
              <p className="text-xs font-medium text-t-primary leading-snug">{item.title}</p>
              {item.summary && (
                <div className="text-[11px] text-t-tertiary mt-0.5 line-clamp-2 leading-relaxed">
                  <InlineMarkdown content={item.summary} />
                </div>
              )}
              {item.created_at && (
                <p className="text-[10px] text-t-tertiary mt-1">
                  {formatTimeAgo(item.created_at)}
                </p>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTimeAgo(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}

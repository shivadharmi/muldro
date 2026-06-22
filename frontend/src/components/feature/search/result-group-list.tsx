"use client";

import type { SearchResult } from "@/lib/types";
import { sourceDbBadge } from "@/lib/design-tokens";
import { EmptyState } from "@/components/ui/empty-state";

interface Props {
  groups: Record<string, SearchResult[]>;
  onSelect: (result: SearchResult) => void;
}

export function ResultGroupList({ groups, onSelect }: Props) {
  const entries = Object.entries(groups);

  if (entries.length === 0) {
    return (
      <EmptyState title="No results found" description="Try adjusting your search query" />
    );
  }

  return (
    <div className="space-y-4">
      {entries.map(([type, results]) => (
        <section key={type} aria-label={`${type} results`}>
          <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            {type}s ({results.length})
          </h3>
          <ul className="space-y-1">
            {results.map((r) => (
              <li key={`${r.id}-${r.source_db ?? "unknown"}`}>
                <button
                  onClick={() => onSelect(r)}
                  className="w-full text-left p-2 rounded-[var(--radius-sm)] hover:bg-surface-2 transition-colors duration-150 cursor-pointer"
                >
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm text-t-primary truncate flex-1">
                      {r.title}
                    </p>
                    {r.source_db && (
                      <span
                        className={`shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded-[var(--radius-sm)] ${sourceDbBadge(r.source_db).style}`}
                        title={r.why_matched ?? undefined}
                      >
                        {sourceDbBadge(r.source_db).label}
                      </span>
                    )}
                  </div>
                  {r.summary && (
                    <p className="text-xs text-t-tertiary truncate mt-0.5">
                      {r.summary}
                    </p>
                  )}
                  {r.why_matched && (
                    <p className="text-xs text-t-tertiary mt-0.5">
                      {r.why_matched}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

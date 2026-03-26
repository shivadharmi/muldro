"use client";

import type { SearchResult } from "@/lib/types";

const SOURCE_DB_COLORS: Record<string, string> = {
  qdrant: "bg-blue-500/15 text-blue-400",
  postgres_fts: "bg-green-500/15 text-green-400",
  neo4j: "bg-purple-500/15 text-purple-400",
};

interface Props {
  groups: Record<string, SearchResult[]>;
  onSelect: (result: SearchResult) => void;
}

export function ResultGroupList({ groups, onSelect }: Props) {
  const entries = Object.entries(groups);

  if (entries.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-t-tertiary">
        No results found.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {entries.map(([type, results]) => (
        <section key={type}>
          <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            {type}s ({results.length})
          </h3>
          <ul className="space-y-1">
            {results.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => onSelect(r)}
                  className="w-full text-left p-2 rounded-[var(--radius-sm)] hover:bg-surface-1 transition-colors cursor-pointer"
                >
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm text-t-primary truncate flex-1">
                      {r.title}
                    </p>
                    {r.source_db && (
                      <span
                        className={`shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded ${SOURCE_DB_COLORS[r.source_db] ?? "bg-surface-2 text-t-tertiary"}`}
                      >
                        {r.source_db}
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

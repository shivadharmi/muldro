"use client";

import type { SearchResult } from "@/lib/types";
import { EmptyState } from "@/components/ui/empty-state";

const SOURCE_DB_LABELS: Record<string, string> = {
  qdrant: "Vector (Qdrant)",
  postgres_fts: "Keyword (Postgres FTS)",
  neo4j: "Graph (Neo4j)",
};

interface Props {
  result: SearchResult | null;
}

export function ResultDetailPane({ result }: Props) {
  if (!result) {
    return (
      <EmptyState title="Select a result" description="Choose an item from the list to view details" />
    );
  }

  return (
    <div className="p-4 space-y-3">
      <div>
        <span className="text-xs text-t-tertiary uppercase tracking-wider">
          {result.type}
        </span>
        <h3 className="text-base font-medium text-t-primary mt-0.5">
          {result.title}
        </h3>
      </div>

      {result.summary && (
        <p className="text-sm text-t-secondary">{result.summary}</p>
      )}

      <div className="flex flex-wrap gap-2 text-xs">
        {result.source_db && (
          <span className="px-2 py-0.5 rounded bg-surface-2 text-t-secondary">
            {SOURCE_DB_LABELS[result.source_db] ?? result.source_db}
          </span>
        )}
        {result.score != null && (
          <span className="px-2 py-0.5 rounded bg-surface-2 text-t-secondary">
            Score: {result.score.toFixed(3)}
          </span>
        )}
      </div>

      {result.why_matched && (
        <div className="text-xs text-t-tertiary">
          Matched: {result.why_matched}
        </div>
      )}
    </div>
  );
}

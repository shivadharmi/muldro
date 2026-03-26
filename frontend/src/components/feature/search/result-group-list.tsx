"use client";

interface SearchResult {
  result_type: string;
  result_id: string;
  title: string;
  snippet: string;
  score: number;
  why_matched: string;
  actions: { action: string; url: string }[];
  metadata: Record<string, unknown>;
}

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
              <li key={r.result_id}>
                <button
                  onClick={() => onSelect(r)}
                  className="w-full text-left p-2 rounded-[var(--radius-sm)] hover:bg-surface-1 transition-colors cursor-pointer"
                >
                  <p className="text-sm text-t-primary truncate">{r.title}</p>
                  {r.snippet && (
                    <p className="text-xs text-t-tertiary truncate mt-0.5">
                      {r.snippet}
                    </p>
                  )}
                  <p className="text-xs text-t-tertiary mt-0.5">
                    {r.why_matched}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

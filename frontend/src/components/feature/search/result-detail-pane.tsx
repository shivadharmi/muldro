"use client";

import Link from "next/link";

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
  result: SearchResult | null;
}

export function ResultDetailPane({ result }: Props) {
  if (!result) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-t-tertiary">
        Select a result to view details
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      <div>
        <span className="text-xs text-t-tertiary uppercase tracking-wider">
          {result.result_type}
        </span>
        <h3 className="text-base font-medium text-t-primary mt-0.5">
          {result.title}
        </h3>
      </div>

      {result.snippet && (
        <p className="text-sm text-t-secondary">{result.snippet}</p>
      )}

      <div className="text-xs text-t-tertiary">
        Matched: {result.why_matched}
      </div>

      {Object.keys(result.metadata).length > 0 && (
        <div className="text-xs text-t-tertiary space-y-0.5">
          {Object.entries(result.metadata).map(([k, v]) => (
            <div key={k}>
              <span className="capitalize">{k}</span>: {String(v)}
            </div>
          ))}
        </div>
      )}

      {result.actions.length > 0 && (
        <div className="flex gap-2 pt-2">
          {result.actions.map((a, i) => (
            <Link
              key={i}
              href={a.url}
              className="px-3 py-1.5 text-xs rounded-[var(--radius-sm)] bg-accent-primary text-white hover:opacity-90 transition-opacity"
            >
              {a.action.charAt(0).toUpperCase() + a.action.slice(1)}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

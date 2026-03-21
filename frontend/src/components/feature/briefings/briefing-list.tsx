"use client";

interface BriefingItem {
  briefing_id: string;
  headline: string | null;
  date: string | null;
  status: string | null;
  domain: string | null;
  confidence: number | null;
  created_at: string | null;
}

interface Props {
  briefings: BriefingItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function BriefingList({ briefings, selectedId, onSelect }: Props) {
  if (briefings.length === 0) {
    return (
      <div className="p-4 text-sm text-t-tertiary">No briefings found.</div>
    );
  }

  return (
    <ul className="divide-y divide-b-primary">
      {briefings.map((b) => (
        <li key={b.briefing_id}>
          <button
            onClick={() => onSelect(b.briefing_id)}
            className={`w-full text-left p-3 hover:bg-surface-1 transition-colors cursor-pointer ${
              selectedId === b.briefing_id ? "bg-surface-1" : ""
            }`}
          >
            <p className="text-sm font-medium text-t-primary truncate">
              {b.headline || "Untitled briefing"}
            </p>
            <div className="flex items-center gap-2 mt-0.5">
              {b.date && (
                <span className="text-xs text-t-tertiary">{b.date}</span>
              )}
              {b.domain && (
                <span className="text-xs text-t-tertiary capitalize">
                  {b.domain}
                </span>
              )}
              {b.status && b.status !== "active" && (
                <span className="text-xs text-accent-primary capitalize">
                  {b.status}
                </span>
              )}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

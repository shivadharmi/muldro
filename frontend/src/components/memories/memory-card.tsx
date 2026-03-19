"use client";

interface MemoryCardProps {
  memory: {
    memory_id: string;
    memory_type: string;
    scope: string | null;
    fact_text: string;
    confidence: number;
    status: string;
    last_accessed_at: string | null;
    is_stale: boolean;
    entity_ids: string[];
    access_count: number;
    created_at: string | null;
  };
}

export function MemoryCard({ memory }: MemoryCardProps) {
  const confidencePct = Math.round(memory.confidence * 100);
  const typeColors: Record<string, string> = {
    semantic: "bg-j-primary-soft text-j-primary",
    episodic: "bg-j-secondary-soft text-j-secondary",
    preference: "bg-j-accent-soft text-j-accent",
    relationship: "bg-j-warning-soft text-j-warning",
    task_context: "bg-j-info-soft text-j-info",
    procedural: "bg-surface-3 text-t-tertiary",
  };

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-t-primary leading-relaxed flex-1">{memory.fact_text}</p>
        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${typeColors[memory.memory_type] ?? typeColors.procedural}`}>
          {memory.memory_type}
        </span>
      </div>

      {/* Confidence bar */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-t-muted w-16">Confidence</span>
        <div className="flex-1 h-1.5 bg-surface-3 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-j-primary"
            style={{ width: `${confidencePct}%` }}
          />
        </div>
        <span className="text-[10px] text-t-tertiary w-8 text-right">{confidencePct}%</span>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-3 text-[10px] text-t-muted">
        {/* Freshness */}
        <span className="flex items-center gap-1">
          <span className={`w-1.5 h-1.5 rounded-full ${memory.is_stale ? "bg-j-warning" : "bg-j-success"}`} />
          {memory.is_stale ? "Stale" : "Fresh"}
        </span>

        {/* Access count */}
        {memory.access_count > 0 && (
          <span>Referenced in {memory.access_count} trace{memory.access_count !== 1 ? "s" : ""}</span>
        )}

        {/* Entity links */}
        {memory.entity_ids.length > 0 && (
          <span>{memory.entity_ids.length} linked entit{memory.entity_ids.length !== 1 ? "ies" : "y"}</span>
        )}
      </div>
    </div>
  );
}

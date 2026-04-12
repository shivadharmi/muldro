"use client";

import type { KnowledgeMemoryItem } from "@/lib/api";

// ── Memory type styling ──────────────────────────────────────────

const MEMORY_TYPE_STYLES: Record<string, { colorClass: string; bgClass: string; letter: string }> = {
  semantic:      { colorClass: "text-j-secondary",  bgClass: "bg-j-secondary-soft", letter: "S" },
  episodic:      { colorClass: "text-j-primary",    bgClass: "bg-j-primary-soft",   letter: "E" },
  preference:    { colorClass: "text-j-warning",    bgClass: "bg-j-warning-soft",   letter: "P" },
  goal:          { colorClass: "text-j-accent",     bgClass: "bg-j-accent-soft",    letter: "G" },
  relationship:  { colorClass: "text-t-muted",      bgClass: "bg-surface-3",        letter: "R" },
  procedural:    { colorClass: "text-t-tertiary",   bgClass: "bg-surface-3",        letter: "X" },
  task_context:  { colorClass: "text-j-error",      bgClass: "bg-j-error-soft",     letter: "T" },
  briefing_item: { colorClass: "text-j-primary",    bgClass: "bg-j-primary-soft",   letter: "B" },
};

function getTypeStyle(memoryType: string) {
  return MEMORY_TYPE_STYLES[memoryType] ?? { colorClass: "text-t-muted", bgClass: "bg-surface-3", letter: "?" };
}

// ── Helpers ───────────────────────────────────────────────────────

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

// ── Component ─────────────────────────────────────────────────────

interface MemoryRowProps {
  memory: KnowledgeMemoryItem;
  selected: boolean;
  onSelect: () => void;
  onEntityClick: (entityId: string) => void;
}

export function MemoryRow({ memory, selected, onSelect, onEntityClick }: MemoryRowProps) {
  const style = getTypeStyle(memory.memory_type);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`flex items-start gap-2 sm:gap-3 px-3 py-2 sm:px-4 sm:py-3 border-b border-b-secondary cursor-pointer transition-colors hover:bg-surface-2 ${
        selected ? "bg-j-primary-soft border-l-2 border-l-j-primary" : ""
      }`}
    >
      {/* Type icon */}
      <div
        className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0 ${style.bgClass} ${style.colorClass}`}
      >
        {style.letter}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Fact text */}
        <p className="text-sm text-t-primary leading-relaxed">
          {memory.fact_text}
        </p>

        {/* Metadata row */}
        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <span className={`text-xs font-medium capitalize ${style.colorClass}`}>
            {memory.memory_type.replace(/_/g, " ")}
          </span>

          {/* Confidence bar */}
          <div className="flex items-center gap-1">
            <div className="w-12 h-1 rounded-full bg-surface-3 overflow-hidden">
              <div
                className="h-full rounded-full bg-j-primary"
                style={{ width: `${Math.round(memory.confidence * 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-t-muted tabular-nums">
              {(memory.confidence * 100).toFixed(0)}%
            </span>
          </div>

          {/* Stability score */}
          <span className="text-[10px] text-t-muted tabular-nums">
            stab {(memory.stability_score * 100).toFixed(0)}%
          </span>

          {/* Relative time */}
          {memory.created_at && (
            <span className="text-[10px] text-t-muted">
              {relativeTime(memory.created_at)}
            </span>
          )}
        </div>

        {/* Entity chips */}
        {(memory.entity_names ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {(memory.entity_names ?? []).map((name, idx) => (
              <button
                key={(memory.entity_ids ?? [])[idx] ?? name}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  const entityId = (memory.entity_ids ?? [])[idx];
                  if (entityId) {
                    onEntityClick(entityId);
                  }
                }}
                className="px-1.5 py-0.5 rounded text-[10px] bg-surface-3 text-t-tertiary cursor-pointer hover:text-t-primary transition-colors min-h-[28px] inline-flex items-center"
              >
                {name}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

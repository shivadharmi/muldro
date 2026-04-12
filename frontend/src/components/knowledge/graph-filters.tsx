"use client";

import { useKnowledgeStore } from "@/stores/knowledge-store";

const ENTITY_TYPE_COLORS: Record<string, { active: string; dot: string }> = {
  person: {
    active: "border-j-primary text-j-primary bg-j-primary-soft",
    dot: "bg-j-primary",
  },
  organization: {
    active: "border-j-secondary text-j-secondary bg-j-secondary-soft",
    dot: "bg-j-secondary",
  },
  project: {
    active: "border-j-accent text-j-accent bg-j-accent-soft",
    dot: "bg-j-accent",
  },
  document: {
    active: "border-j-warning text-j-warning bg-j-warning-soft",
    dot: "bg-j-warning",
  },
  repository: {
    active: "border-j-error text-j-error bg-j-error-soft",
    dot: "bg-j-error",
  },
};

const FILTER_TYPES = [
  "person",
  "organization",
  "project",
  "document",
  "repository",
] as const;

export function GraphFilters() {
  const hiddenTypes = useKnowledgeStore((s) => s.hiddenTypes);
  const toggleTypeFilter = useKnowledgeStore((s) => s.toggleTypeFilter);

  const allActive = hiddenTypes.size === 0;

  function handleAllClick() {
    // Clear all hidden types by toggling each one that's currently hidden
    for (const t of hiddenTypes) {
      toggleTypeFilter(t);
    }
  }

  function handleTypeClick(type: string) {
    toggleTypeFilter(type);
  }

  return (
    <div className="shrink-0 flex items-center gap-1.5 sm:gap-2 flex-wrap px-3 sm:px-4 py-2 sm:py-2.5 bg-surface-1 border-b border-b-secondary">
      {/* "All" chip */}
      <button
        type="button"
        onClick={handleAllClick}
        className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-medium border cursor-pointer transition-colors min-h-[32px] ${
          allActive
            ? "border-j-primary text-j-primary bg-j-primary-soft"
            : "border-b-secondary text-t-tertiary bg-surface-2"
        }`}
      >
        All
      </button>

      {/* Type-specific chips */}
      {FILTER_TYPES.map((type) => {
        const isHidden = hiddenTypes.has(type);
        const colors = ENTITY_TYPE_COLORS[type];

        return (
          <button
            key={type}
            type="button"
            onClick={() => handleTypeClick(type)}
            className={`px-2.5 sm:px-3 py-1 rounded-full text-xs font-medium border cursor-pointer transition-colors inline-flex items-center gap-1.5 min-h-[32px] ${
              isHidden
                ? "border-b-secondary text-t-tertiary bg-surface-2"
                : colors.active
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isHidden ? "bg-t-muted" : colors.dot
              }`}
            />
            <span className="capitalize">{type}</span>
          </button>
        );
      })}
    </div>
  );
}

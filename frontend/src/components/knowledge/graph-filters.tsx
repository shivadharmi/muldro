"use client";

import { useKnowledgeStore } from "@/stores/knowledge-store";

const ENTITY_TYPE_COLORS: Record<string, { active: string; dot: string }> = {
  person: {
    active:
      "border-[hsl(193_100%_66%)] text-[hsl(193_100%_66%)] bg-[hsl(193_100%_66%/0.1)]",
    dot: "bg-[hsl(193_100%_66%)]",
  },
  organization: {
    active:
      "border-[hsl(247_92%_74%)] text-[hsl(247_92%_74%)] bg-[hsl(247_92%_74%/0.1)]",
    dot: "bg-[hsl(247_92%_74%)]",
  },
  project: {
    active:
      "border-[hsl(159_78%_54%)] text-[hsl(159_78%_54%)] bg-[hsl(159_78%_54%/0.1)]",
    dot: "bg-[hsl(159_78%_54%)]",
  },
  document: {
    active:
      "border-[hsl(36_100%_64%)] text-[hsl(36_100%_64%)] bg-[hsl(36_100%_64%/0.1)]",
    dot: "bg-[hsl(36_100%_64%)]",
  },
  repository: {
    active:
      "border-[hsl(351_100%_71%)] text-[hsl(351_100%_71%)] bg-[hsl(351_100%_71%/0.1)]",
    dot: "bg-[hsl(351_100%_71%)]",
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
    <div className="shrink-0 flex items-center gap-2 flex-wrap px-4 py-2.5 bg-surface-1 border-b border-b-secondary">
      {/* "All" chip */}
      <button
        type="button"
        onClick={handleAllClick}
        className={`px-3 py-1 rounded-full text-xs font-medium border cursor-pointer transition-colors ${
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
            className={`px-3 py-1 rounded-full text-xs font-medium border cursor-pointer transition-colors inline-flex items-center gap-1.5 ${
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

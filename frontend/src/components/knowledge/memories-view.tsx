"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchKnowledgeMemories } from "@/lib/api";
import type { KnowledgeMemoryItem } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { MemoryRow } from "./memory-row";
import { MemoryDetailPanel } from "./memory-detail-panel";

// ── Memory types for filter chips ─────────────────────────────────

const MEMORY_TYPES = [
  { key: "semantic",      label: "Semantic",      colorClass: "text-j-secondary",  borderClass: "border-j-secondary",  bgClass: "bg-j-secondary-soft" },
  { key: "episodic",      label: "Episodic",      colorClass: "text-j-primary",    borderClass: "border-j-primary",    bgClass: "bg-j-primary-soft" },
  { key: "preference",    label: "Preference",    colorClass: "text-j-warning",    borderClass: "border-j-warning",    bgClass: "bg-j-warning-soft" },
  { key: "goal",          label: "Goal",          colorClass: "text-j-accent",     borderClass: "border-j-accent",     bgClass: "bg-j-accent-soft" },
  { key: "relationship",  label: "Relationship",  colorClass: "text-t-muted",      borderClass: "border-t-muted",      bgClass: "bg-surface-3" },
  { key: "procedural",    label: "Procedural",    colorClass: "text-t-tertiary",   borderClass: "border-t-tertiary",   bgClass: "bg-surface-3" },
  { key: "task_context",  label: "Task Context",  colorClass: "text-j-error",      borderClass: "border-j-error",      bgClass: "bg-j-error-soft" },
  { key: "briefing_item", label: "Briefing",      colorClass: "text-j-primary",    borderClass: "border-j-primary",    bgClass: "bg-j-primary-soft" },
] as const;

type SortOption = "recent" | "confidence" | "stability";

const SORT_OPTIONS: { key: SortOption; label: string }[] = [
  { key: "recent", label: "Recent" },
  { key: "confidence", label: "Confidence" },
  { key: "stability", label: "Stability" },
];

// ── Component ─────────────────────────────────────────────────────

export function MemoriesView() {
  const memoryTypeFilter = useKnowledgeStore((s) => s.memoryTypeFilter);
  const setMemoryTypeFilter = useKnowledgeStore((s) => s.setMemoryTypeFilter);
  const memorySortBy = useKnowledgeStore((s) => s.memorySortBy);
  const setMemorySortBy = useKnowledgeStore((s) => s.setMemorySortBy);
  const selectedMemoryId = useKnowledgeStore((s) => s.selectedMemoryId);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);
  const searchQuery = useKnowledgeStore((s) => s.searchQuery);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);

  // Build a filter key to detect when filters change
  const filterKey = `${memoryTypeFilter}|${memorySortBy}|${searchQuery}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  const [page, setPage] = useState(1);

  // Render-phase reset when filters change (React-recommended pattern for
  // "adjust state when props change" — avoids setState-in-effect).
  if (prevFilterKey !== filterKey) {
    setPrevFilterKey(filterKey);
    setPage(1);
  }

  const scrollRef = useRef<HTMLDivElement>(null);

  // Cache per-(filterKey, page) results so we can accumulate without an effect.
  const pageCache = useRef<Map<string, KnowledgeMemoryItem[]>>(new Map());
  // Clear cache when filters change (ref mutation during render is safe).
  if (prevFilterKey !== filterKey) {
    pageCache.current = new Map();
  }

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["knowledge-memories", memoryTypeFilter, memorySortBy, searchQuery, page],
    queryFn: () =>
      fetchKnowledgeMemories({
        type: memoryTypeFilter || undefined,
        sort_by: memorySortBy,
        search: searchQuery || undefined,
        page,
        limit: 50,
      }),
  });

  // Store current page results in the cache (ref mutation during render is safe).
  if (data?.items) {
    pageCache.current.set(`${filterKey}:${page}`, data.items);
  }

  // Derive accumulated items from the per-page cache.
  const allItems = useMemo(() => {
    const result: KnowledgeMemoryItem[] = [];
    const seen = new Set<string>();
    for (let p = 1; p <= page; p++) {
      for (const item of pageCache.current.get(`${filterKey}:${p}`) ?? []) {
        if (!seen.has(item.memory_id)) {
          seen.add(item.memory_id);
          result.push(item);
        }
      }
    }
    return result;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, page, data?.items]);

  const hasMore = data ? page < data.pages : false;

  // Infinite scroll handler
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || isFetching || !hasMore) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    if (nearBottom) {
      setPage((prev) => prev + 1);
    }
  }, [isFetching, hasMore]);

  // Entity click navigates to graph tab
  const handleEntityClick = useCallback(
    (entityId: string) => {
      setActiveTab("graph");
      selectEntity(entityId);
    },
    [setActiveTab, selectEntity],
  );

  return (
    <div className="flex h-full relative">
      {/* Left side: filters + list */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Filter bar */}
        <div className="px-3 sm:px-4 py-2 sm:py-3 border-b border-b-secondary space-y-2">
          {/* Type filters */}
          <div className="flex flex-wrap gap-1.5">
            <FilterChip
              label="All"
              active={memoryTypeFilter === null}
              onClick={() => setMemoryTypeFilter(null)}
            />
            {MEMORY_TYPES.map((mt) => (
              <FilterChip
                key={mt.key}
                label={mt.label}
                active={memoryTypeFilter === mt.key}
                activeColorClass={mt.colorClass}
                activeBorderClass={mt.borderClass}
                activeBgClass={mt.bgClass}
                onClick={() =>
                  setMemoryTypeFilter(memoryTypeFilter === mt.key ? null : mt.key)
                }
              />
            ))}
          </div>

          {/* Sort options */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-t-muted mr-1">Sort:</span>
            {SORT_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => setMemorySortBy(opt.key)}
                className={`px-3 py-1 rounded-full text-xs cursor-pointer transition-colors ${
                  memorySortBy === opt.key
                    ? "bg-j-primary-soft text-j-primary border border-j-primary"
                    : "border border-b-secondary text-t-tertiary bg-surface-2 hover:text-t-secondary"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Memory list */}
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto"
        >
          {isLoading && allItems.length === 0 ? (
            <div className="flex items-center justify-center h-32">
              <p className="text-sm text-t-tertiary">Loading memories...</p>
            </div>
          ) : allItems.length === 0 ? (
            <div className="flex items-center justify-center h-32">
              <div className="text-center">
                <p className="text-sm text-t-tertiary">No memories found</p>
                <p className="text-xs text-t-muted mt-1">
                  {searchQuery
                    ? "Try a different search term"
                    : "Memories will appear as Jarvis processes your data"}
                </p>
              </div>
            </div>
          ) : (
            <>
              {allItems.map((memory) => (
                <MemoryRow
                  key={memory.memory_id}
                  memory={memory}
                  selected={memory.memory_id === selectedMemoryId}
                  onSelect={() => selectMemory(memory.memory_id)}
                  onEntityClick={handleEntityClick}
                />
              ))}
              {isFetching && (
                <div className="px-4 py-3 text-center">
                  <p className="text-xs text-t-muted">Loading more...</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Right side: detail panel */}
      {selectedMemoryId && (
        <>
          {/* Mobile backdrop */}
          <div
            className="lg:hidden absolute inset-0 z-10 bg-black/40"
            onClick={() => selectMemory(null)}
            onKeyDown={(e) => { if (e.key === "Escape") selectMemory(null); }}
            role="button"
            tabIndex={-1}
            aria-label="Close panel"
          />
          <MemoryDetailPanel />
        </>
      )}
    </div>
  );
}

// ── Filter chip sub-component ─────────────────────────────────────

interface FilterChipProps {
  label: string;
  active: boolean;
  activeColorClass?: string;
  activeBorderClass?: string;
  activeBgClass?: string;
  onClick: () => void;
}

function FilterChip({
  label,
  active,
  activeColorClass,
  activeBorderClass,
  activeBgClass,
  onClick,
}: FilterChipProps) {
  if (active) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`px-3 py-1 rounded-full text-xs cursor-pointer border transition-colors ${
          activeBorderClass ?? "border-j-primary"
        } ${activeColorClass ?? "text-j-primary"} ${activeBgClass ?? "bg-j-primary-soft"}`}
      >
        {label}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="border border-b-secondary text-t-tertiary bg-surface-2 px-3 py-1 rounded-full text-xs cursor-pointer hover:text-t-secondary transition-colors"
    >
      {label}
    </button>
  );
}

"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchAll } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import type { SearchResult } from "@/lib/types";

// ── Helpers ────────────────────────────────────────────────────────

function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debouncedValue;
}

interface ResultGroup {
  label: string;
  results: SearchResult[];
}

function groupResults(results: SearchResult[]): ResultGroup[] {
  const entities: SearchResult[] = [];
  const memories: SearchResult[] = [];
  const other: SearchResult[] = [];

  for (const r of results) {
    if (r.type === "entity") {
      entities.push(r);
    } else if (r.type === "memory") {
      memories.push(r);
    } else {
      other.push(r);
    }
  }

  const groups: ResultGroup[] = [];
  if (entities.length > 0) groups.push({ label: "Entities", results: entities });
  if (memories.length > 0) groups.push({ label: "Memories", results: memories });
  if (other.length > 0) groups.push({ label: "Other", results: other });
  return groups;
}

function flattenGroups(groups: ResultGroup[]): SearchResult[] {
  const flat: SearchResult[] = [];
  for (const g of groups) {
    for (const r of g.results) {
      flat.push(r);
    }
  }
  return flat;
}

// ── Icons ──────────────────────────────────────────────────────────

function SearchIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      className="shrink-0 text-t-muted"
    >
      <path
        d="M7.333 12.667A5.333 5.333 0 1 0 7.333 2a5.333 5.333 0 0 0 0 10.667ZM14 14l-2.9-2.9"
        stroke="currentColor"
        strokeWidth="1.33"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function EntityIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className="shrink-0 text-[hsl(193_100%_66%)]">
      <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="7" cy="7" r="2" fill="currentColor" />
    </svg>
  );
}

function MemoryIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className="shrink-0 text-[hsl(247_92%_74%)]">
      <path
        d="M7 1.75v10.5M3.5 4.083h7M4.375 7h5.25M5.25 9.917h3.5"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function OtherIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className="shrink-0 text-t-tertiary">
      <rect x="2" y="2" width="10" height="10" rx="2" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function iconForType(type: string) {
  if (type === "entity") return <EntityIcon />;
  if (type === "memory") return <MemoryIcon />;
  return <OtherIcon />;
}

// ── Component ──────────────────────────────────────────────────────

export function KnowledgeSearch() {
  const [inputValue, setInputValue] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const [isFocused, setIsFocused] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);
  const setSearchQuery = useKnowledgeStore((s) => s.setSearchQuery);

  const debouncedQuery = useDebounce(inputValue, 300);

  // Sync debounced query to store for graph/memories filtering
  useEffect(() => {
    setSearchQuery(debouncedQuery);
  }, [debouncedQuery, setSearchQuery]);

  const { data, isFetching } = useQuery({
    queryKey: ["knowledge-search", debouncedQuery],
    queryFn: () => searchAll(debouncedQuery, undefined, 20),
    enabled: debouncedQuery.length > 1,
  });

  const groups = useMemo(
    () => (data?.results ? groupResults(data.results) : []),
    [data?.results],
  );

  const flatResults = useMemo(() => flattenGroups(groups), [groups]);

  // Open dropdown when we have results
  useEffect(() => {
    if (groups.length > 0 && debouncedQuery.length > 1) {
      setIsOpen(true);
    }
  }, [groups, debouncedQuery]);

  // Reset highlight when results change
  useEffect(() => {
    setHighlightIndex(-1);
  }, [flatResults.length]);

  // Global "/" shortcut to focus input
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (
        e.key === "/" &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        document.activeElement?.tagName !== "INPUT" &&
        document.activeElement?.tagName !== "TEXTAREA" &&
        !(document.activeElement as HTMLElement)?.isContentEditable
      ) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Click-outside to close dropdown
  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, []);

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlightIndex < 0 || !listRef.current) return;
    const items = listRef.current.querySelectorAll("[data-result-index]");
    const target = items[highlightIndex];
    if (target) {
      target.scrollIntoView({ block: "nearest" });
    }
  }, [highlightIndex]);

  const handleSelect = useCallback(
    (result: SearchResult) => {
      if (result.type === "entity") {
        setActiveTab("graph");
        selectEntity(result.id);
      } else if (result.type === "memory") {
        setActiveTab("memories");
        selectMemory(result.id);
      }
      setIsOpen(false);
      inputRef.current?.blur();
    },
    [setActiveTab, selectEntity, selectMemory],
  );

  function handleInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setIsOpen(false);
      inputRef.current?.blur();
      return;
    }

    if (!isOpen || flatResults.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIndex((prev) =>
        prev < flatResults.length - 1 ? prev + 1 : 0,
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIndex((prev) =>
        prev > 0 ? prev - 1 : flatResults.length - 1,
      );
    } else if (e.key === "Enter" && highlightIndex >= 0) {
      e.preventDefault();
      handleSelect(flatResults[highlightIndex]);
    }
  }

  // Track a running index across groups for highlight matching
  let runningIndex = 0;

  return (
    <div ref={wrapperRef} className="relative min-w-64">
      {/* Input */}
      <div className="relative flex items-center">
        <div className="absolute left-3 pointer-events-none">
          <SearchIcon />
        </div>
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value);
            if (e.target.value.length <= 1) {
              setIsOpen(false);
            }
          }}
          onFocus={() => {
            setIsFocused(true);
            if (flatResults.length > 0 && inputValue.length > 1) {
              setIsOpen(true);
            }
          }}
          onBlur={() => setIsFocused(false)}
          onKeyDown={handleInputKeyDown}
          placeholder="Search knowledge..."
          className="w-full bg-surface-2 border border-b-secondary rounded-[var(--radius-sm)] pl-9 pr-16 py-1.5 text-sm text-t-primary placeholder:text-t-muted focus:outline-none focus:border-j-primary"
          aria-label="Search knowledge base"
          aria-expanded={isOpen}
          aria-controls="knowledge-search-dropdown"
          role="combobox"
          aria-autocomplete="list"
          aria-activedescendant={
            highlightIndex >= 0
              ? `knowledge-result-${highlightIndex}`
              : undefined
          }
        />
        {/* Hint or loading indicator */}
        <div className="absolute right-3 pointer-events-none">
          {isFetching ? (
            <span className="text-xs text-t-muted animate-pulse">...</span>
          ) : !isFocused && inputValue.length === 0 ? (
            <kbd className="text-[10px] text-t-muted bg-surface-1 border border-b-secondary rounded px-1.5 py-0.5 font-mono">
              /
            </kbd>
          ) : null}
        </div>
      </div>

      {/* Dropdown */}
      {isOpen && groups.length > 0 && (
        <div
          id="knowledge-search-dropdown"
          ref={listRef}
          role="listbox"
          className="absolute top-full left-0 right-0 mt-1 bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] shadow-lg z-50 overflow-hidden max-h-80 overflow-y-auto"
        >
          {groups.map((group) => {
            const groupItems = group.results.map((result, i) => {
              const itemIndex = runningIndex + i;
              const isHighlighted = itemIndex === highlightIndex;

              return (
                <button
                  key={result.id}
                  id={`knowledge-result-${itemIndex}`}
                  data-result-index={itemIndex}
                  role="option"
                  type="button"
                  aria-selected={isHighlighted}
                  onMouseDown={(e) => {
                    // Prevent blur from closing dropdown before click registers
                    e.preventDefault();
                  }}
                  onClick={() => handleSelect(result)}
                  onMouseEnter={() => setHighlightIndex(itemIndex)}
                  className={`w-full px-3 py-2 text-sm cursor-pointer flex items-center gap-2 text-left transition-colors ${
                    isHighlighted ? "bg-surface-2" : "hover:bg-surface-2"
                  }`}
                >
                  {iconForType(result.type)}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-t-primary">{result.title}</div>
                    {result.summary && (
                      <div className="truncate text-xs text-t-muted mt-0.5">
                        {result.summary}
                      </div>
                    )}
                  </div>
                  {result.score != null && (
                    <span className="text-[10px] text-t-muted tabular-nums shrink-0">
                      {(result.score * 100).toFixed(0)}%
                    </span>
                  )}
                </button>
              );
            });

            runningIndex += group.results.length;

            return (
              <div key={group.label}>
                <div className="px-3 py-1.5 text-xs font-medium text-t-muted uppercase tracking-wider bg-surface-2">
                  {group.label}
                </div>
                {groupItems}
              </div>
            );
          })}
        </div>
      )}

      {/* Empty state */}
      {isOpen && debouncedQuery.length > 1 && !isFetching && groups.length === 0 && data && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] shadow-lg z-50 overflow-hidden">
          <div className="px-3 py-4 text-sm text-t-muted text-center">
            No results for &ldquo;{debouncedQuery}&rdquo;
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchAll } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { SearchBar } from "@/components/search/search-bar";
import { SkeletonCard } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ResultGroupList } from "@/components/feature/search/result-group-list";
import { ResultDetailPane } from "@/components/feature/search/result-detail-pane";
import type { SearchResult } from "@/lib/types";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [selectedResult, setSelectedResult] = useState<SearchResult | null>(
    null
  );

  const { data, isLoading, isError } = useQuery({
    queryKey: ["search", query],
    queryFn: () => searchAll(query, undefined, 20),
    enabled: query.length > 0,
  });

  const handleSearch = useCallback((_query: string) => {
    setQuery(_query);
    setSelectedResult(null);
  }, []);

  // Group flat results by type for the ResultGroupList component
  const groups: Record<string, SearchResult[]> = {};
  if (data?.results) {
    for (const r of data.results) {
      const key = r.type || "unknown";
      if (!groups[key]) groups[key] = [];
      groups[key].push(r);
    }
  }

  const totalCount = data?.results?.length ?? 0;

  return (
    <div className="flex h-full">
      {/* Left: Search + Results */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-4 border-b border-b-primary">
          <PageHeader
            title="Search"
            subtitle="Search across everything"
            variant="collection"
          />
          <div className="mt-3">
            <SearchBar onSearch={(q) => handleSearch(q)} />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {/* Initial state — no search yet */}
          {!data && !isLoading && !isError && query.length === 0 && (
            <EmptyState
              title="Search across everything"
              description="Find memories, entities, events, and documents"
              icon={
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="text-t-muted">
                  <circle cx="14" cy="14" r="8" stroke="currentColor" strokeWidth="2" />
                  <path d="M20 20l6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              }
            />
          )}

          {/* Loading */}
          {isLoading && (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {/* Error */}
          {isError && (
            <EmptyState title="Search failed" description="Something went wrong. Please try again." />
          )}

          {/* Results */}
          {data && !isLoading && (
            <>
              <p className="text-[13px] text-t-secondary font-medium mb-3">
                {totalCount} result{totalCount !== 1 ? "s" : ""} found
              </p>
              <ResultGroupList
                groups={groups}
                onSelect={setSelectedResult}
              />
            </>
          )}
        </div>
      </div>

      {/* Right: Detail */}
      <div className="w-96 shrink-0 border-l border-b-primary hidden lg:block">
        <ResultDetailPane result={selectedResult} />
      </div>

      {/* Mobile detail overlay */}
      {selectedResult && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setSelectedResult(null)}
          />
          <div className="absolute inset-x-0 bottom-0 max-h-[80vh] rounded-t-[var(--radius-xl)] bg-surface-1 border-t border-b-secondary shadow-[var(--shadow-lg)] overflow-y-auto animate-slide-in-up">
            <div className="sticky top-0 flex items-center justify-between px-4 py-3 border-b border-b-secondary bg-surface-1 z-10">
              <span className="text-[13px] font-semibold text-t-primary">Result Details</span>
              <button
                onClick={() => setSelectedResult(null)}
                className="p-1 rounded-[var(--radius-sm)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
                aria-label="Close"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <ResultDetailPane result={selectedResult} />
          </div>
        </div>
      )}
    </div>
  );
}

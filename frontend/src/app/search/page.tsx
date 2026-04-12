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
    </div>
  );
}

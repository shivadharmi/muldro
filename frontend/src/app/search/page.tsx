"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchAll } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { SearchBar } from "@/components/search/search-bar";
import { ResultGroupList } from "@/components/feature/search/result-group-list";
import { ResultDetailPane } from "@/components/feature/search/result-detail-pane";
import type { SearchResult } from "@/lib/types";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [selectedResult, setSelectedResult] = useState<SearchResult | null>(
    null
  );

  const { data, isLoading } = useQuery({
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
          {isLoading && (
            <p className="text-t-tertiary text-sm">Searching...</p>
          )}
          {data && (
            <>
              <p className="text-xs text-t-tertiary mb-3">
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

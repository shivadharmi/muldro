"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchUnified } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { SearchBar } from "@/components/search/search-bar";
import { ResultGroupList } from "@/components/feature/search/result-group-list";
import { ResultDetailPane } from "@/components/feature/search/result-detail-pane";

interface SearchResult {
  result_type: string;
  result_id: string;
  title: string;
  snippet: string;
  score: number;
  why_matched: string;
  actions: { action: string; url: string }[];
  metadata: Record<string, unknown>;
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [selectedResult, setSelectedResult] = useState<SearchResult | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["unified-search", query],
    queryFn: () => searchUnified(query, 20),
    enabled: query.length > 0,
  });

  const handleSearch = useCallback((_query: string) => {
    setQuery(_query);
    setSelectedResult(null);
  }, []);

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
                {data.total_count} result{data.total_count !== 1 ? "s" : ""} found
              </p>
              <ResultGroupList
                groups={data.groups ?? {}}
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

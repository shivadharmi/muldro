"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchKnowledge } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { SearchBar } from "@/components/search/search-bar";
import { SearchResults } from "@/components/search/search-results";

export default function SearchPage() {
  const [searchParams, setSearchParams] = useState<{ query: string; scope: string } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["search", searchParams?.query, searchParams?.scope],
    queryFn: () => searchKnowledge(searchParams!.query, searchParams!.scope),
    enabled: !!searchParams,
  });

  return (
    <div className="p-6">
      <PageHeader title="Search" subtitle="Search memories, entities, and events" />

      <div className="mb-6">
        <SearchBar onSearch={(query, scope) => setSearchParams({ query, scope })} />
      </div>

      {isLoading && <p className="text-neutral-500 text-sm">Searching...</p>}
      {data && <SearchResults results={data.results} />}
    </div>
  );
}

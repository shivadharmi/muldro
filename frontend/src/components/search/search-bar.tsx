"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

export function SearchBar({
  onSearch,
}: {
  onSearch: (query: string, scope: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) onSearch(query.trim(), scope);
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-3">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search memories, entities, events..."
        className="flex-1 bg-surface-2 border border-b-primary rounded-lg px-4 py-2 text-sm text-t-primary placeholder:text-t-muted"
      />
      <select
        value={scope}
        onChange={(e) => setScope(e.target.value)}
        className="bg-surface-2 border border-b-primary rounded px-3 py-2 text-sm text-t-primary"
      >
        <option value="all">All</option>
        <option value="memory">Memory</option>
        <option value="entities">Entities</option>
        <option value="events">Events</option>
      </select>
      <Button type="submit">Search</Button>
    </form>
  );
}

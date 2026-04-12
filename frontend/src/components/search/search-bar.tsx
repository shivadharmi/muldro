"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

const SCOPES = [
  { value: "all", label: "All" },
  { value: "memory", label: "Memories" },
  { value: "entities", label: "Entities" },
  { value: "events", label: "Events" },
];

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
    <form onSubmit={handleSubmit} className="space-y-3 sm:space-y-0 sm:flex sm:items-center sm:gap-3">
      <div className="bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5 shrink-0">
        {SCOPES.map((s) => (
          <button
            key={s.value}
            type="button"
            onClick={() => setScope(s.value)}
            className={`px-2.5 py-1 text-xs rounded-[var(--radius-md)] transition-all duration-150 cursor-pointer ${
              scope === s.value
                ? "bg-j-primary text-j-primary-fg font-medium"
                : "text-t-muted hover:text-t-secondary"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search memories, entities, events..."
        className="flex-1 w-full sm:w-auto bg-surface-2 border border-b-secondary rounded-[var(--radius-lg)] px-4 py-2 text-sm text-t-primary placeholder:text-t-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0"
      />
      <Button type="submit">Search</Button>
    </form>
  );
}

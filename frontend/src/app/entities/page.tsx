"use client";

import { useState, useEffect } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { searchKnowledge } from "@/lib/api";
import { useToast } from "@/components/ui/toast";

const ENTITY_TYPES = ["all", "person", "organization", "project", "meeting", "goal", "topic"];

const typeColors: Record<string, "blue" | "purple" | "green" | "yellow" | "red" | "default"> = {
  person: "blue",
  organization: "purple",
  project: "green",
  meeting: "yellow",
  goal: "red",
  topic: "default",
};

interface Entity {
  entity_id: string;
  entity_type: string;
  canonical_name: string;
  summary?: string;
}

export default function EntitiesPage() {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const { addToast } = useToast();

  // Load initial entities on mount
  useEffect(() => {
    doSearch("*");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doSearch(searchQuery: string) {
    setLoading(true);
    try {
      const data = await searchKnowledge(searchQuery, "entities");
      setEntities(
        (data.results || []).map((r) => ({
          entity_id: r.id,
          entity_type: r.type,
          canonical_name: r.title,
          summary: r.summary ?? undefined,
        }))
      );
      setHasSearched(true);
    } catch (err) {
      addToast(
        `Search failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        "error"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    doSearch(query.trim());
  }

  const filtered =
    typeFilter === "all"
      ? entities
      : entities.filter((e) => e.entity_type === typeFilter);

  return (
    <div className="p-6 space-y-6">
      <PageHeader title="Entities" subtitle="Browse the world model entity graph" />

      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search entities..."
          className="flex-1 rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-2 text-white placeholder-neutral-500 focus:border-blue-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "..." : "Search"}
        </button>
      </form>

      {/* Type filter tabs */}
      <div className="flex gap-1 flex-wrap">
        {ENTITY_TYPES.map((t) => {
          const count = t === "all" ? entities.length : entities.filter((e) => e.entity_type === t).length;
          return (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                typeFilter === t
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-400 hover:text-white"
              }`}
            >
              {t === "all" ? "All" : t}
              {count > 0 && <span className="ml-1 opacity-60">{count}</span>}
            </button>
          );
        })}
      </div>

      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i}>
              <CardBody>
                <div className="animate-pulse space-y-2">
                  <div className="h-4 w-32 bg-neutral-800 rounded" />
                  <div className="h-3 w-24 bg-neutral-800 rounded" />
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((entity) => (
            <Card key={entity.entity_id}>
              <CardBody>
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-medium text-white">
                    {entity.canonical_name}
                  </h3>
                  <Badge variant={typeColors[entity.entity_type] || "default"}>
                    {entity.entity_type}
                  </Badge>
                </div>
                {entity.summary && (
                  <p className="text-xs text-neutral-400 mb-2">{entity.summary}</p>
                )}
                <p className="text-[10px] text-neutral-600 font-mono">{entity.entity_id}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {!loading && filtered.length === 0 && hasSearched && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">No entities found</p>
          <p className="text-neutral-600 text-xs mt-1">
            Try a different search term or entity type.
          </p>
        </div>
      )}

      {!loading && !hasSearched && entities.length === 0 && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">
            Search for entities in the world model
          </p>
        </div>
      )}
    </div>
  );
}

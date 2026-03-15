"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function EntitiesPage() {
  const [query, setQuery] = useState("");
  const [entities, setEntities] = useState<Array<{
    entity_id: string;
    entity_type: string;
    canonical_name: string;
    attributes?: Record<string, unknown>;
  }>>([]);
  const [loading, setLoading] = useState(false);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, scope: "entities" }),
      });
      const data = await res.json();
      setEntities(data.entities || data.results || []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  const typeColors: Record<string, string> = {
    person: "blue",
    organization: "purple",
    project: "green",
    meeting: "yellow",
    goal: "orange",
  };

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

      {entities.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {entities.map((entity) => (
            <Card key={entity.entity_id}>
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-medium text-white">
                    {entity.canonical_name}
                  </h3>
                  <Badge variant={(typeColors[entity.entity_type] as "blue" | "purple" | "green" | "yellow") || "default"}>
                    {entity.entity_type}
                  </Badge>
                </div>
                <p className="text-xs text-neutral-500 font-mono">{entity.entity_id}</p>
                {entity.attributes && (
                  <div className="mt-2 space-y-1">
                    {Object.entries(entity.attributes).slice(0, 3).map(([k, v]) => (
                      <div key={k} className="text-xs text-neutral-400">
                        <span className="text-neutral-500">{k}:</span>{" "}
                        {String(v)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {entities.length === 0 && !loading && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          Search for entities in the world model
        </div>
      )}
    </div>
  );
}

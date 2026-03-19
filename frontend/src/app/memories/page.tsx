"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { MemoryCard } from "@/components/memories/memory-card";
import { fetchMemories } from "@/lib/api";
import type { MemoryItem } from "@/lib/types";

const MEMORY_TYPES = ["all", "episodic", "semantic", "preference", "procedural", "relationship", "task_context"];

export default function MemoriesPage() {
  const [selectedType, setSelectedType] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [groupByType, setGroupByType] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["memories", selectedType],
    queryFn: () =>
      fetchMemories(selectedType === "all" ? undefined : selectedType, 50),
    refetchInterval: 60_000,
  });

  const allMemories = data?.memories || [];
  const memories = searchQuery.trim()
    ? allMemories.filter((mem) => {
        const text = (mem.fact_text || "").toLowerCase();
        return text.includes(searchQuery.toLowerCase());
      })
    : allMemories;

  const typeCounts = allMemories.reduce<Record<string, number>>((acc, mem) => {
    acc[mem.memory_type] = (acc[mem.memory_type] || 0) + 1;
    return acc;
  }, {});

  const grouped = groupByType
    ? MEMORY_TYPES.filter((t) => t !== "all").reduce<Record<string, MemoryItem[]>>((acc, t) => {
        const items = memories.filter((m) => m.memory_type === t);
        if (items.length > 0) acc[t] = items;
        return acc;
      }, {})
    : null;

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Memories"
        subtitle="Browse the knowledge base — facts, preferences, and learned patterns"
        variant="collection"
      />

      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search memories..."
          className="flex-1 rounded-lg bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-1 focus:ring-j-ring"
        />
        <button
          onClick={() => setGroupByType((prev) => !prev)}
          className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
            groupByType
              ? "bg-j-primary text-j-primary-fg"
              : "bg-surface-2 text-t-secondary hover:text-t-primary"
          }`}
        >
          {groupByType ? "Flat list" : "Group by type"}
        </button>
        <div className="flex gap-2 flex-wrap">
          {MEMORY_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setSelectedType(t)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                selectedType === t
                  ? "bg-j-primary text-j-primary-fg"
                  : "bg-surface-2 text-t-secondary hover:text-t-primary"
              }`}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
              {t !== "all" && typeCounts[t] != null && (
                <span className="ml-1 text-[10px] opacity-60">{typeCounts[t]}</span>
              )}
              {t === "all" && allMemories.length > 0 && (
                <span className="ml-1 text-[10px] opacity-60">{allMemories.length}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-b-primary bg-surface-1 p-3 animate-pulse">
              <div className="h-3 w-16 bg-surface-2 rounded mb-2" />
              <div className="h-4 w-full bg-surface-2 rounded mb-1" />
              <div className="h-4 w-3/4 bg-surface-2 rounded" />
              <div className="mt-2 h-1 bg-surface-2 rounded-full" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && memories.length === 0 && (
        <div className="text-center py-12">
          <p className="text-t-tertiary text-sm font-medium">No memories found</p>
          <p className="text-t-muted text-xs mt-1">
            Memories are created as Jarvis processes events and learns from interactions.
          </p>
        </div>
      )}

      {grouped ? (
        <div className="space-y-6">
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type}>
              <h3 className="text-sm font-medium text-t-secondary mb-2 capitalize">{type}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {items.map((mem: MemoryItem, i: number) => (
                  <MemoryCard
                    key={mem.memory_id || i}
                    memory={{
                      memory_id: mem.memory_id || `mem-${i}`,
                      memory_type: mem.memory_type || "",
                      scope: mem.scope ?? null,
                      fact_text: mem.fact_text || "",
                      confidence: mem.confidence ?? 1.0,
                      status: mem.status ?? "active",
                      last_accessed_at: mem.last_accessed_at ?? null,
                      is_stale: mem.is_stale ?? false,
                      entity_ids: mem.entity_ids ?? [],
                      access_count: mem.access_count ?? 0,
                      created_at: mem.created_at ?? null,
                    }}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {memories.map((mem: MemoryItem, i: number) => (
            <MemoryCard
              key={mem.memory_id || i}
              memory={{
                memory_id: mem.memory_id || `mem-${i}`,
                memory_type: mem.memory_type || "",
                scope: mem.scope ?? null,
                fact_text: mem.fact_text || "",
                confidence: mem.confidence ?? 1.0,
                status: mem.status ?? "active",
                last_accessed_at: mem.last_accessed_at ?? null,
                is_stale: mem.is_stale ?? false,
                entity_ids: mem.entity_ids ?? [],
                access_count: mem.access_count ?? 0,
                created_at: mem.created_at ?? null,
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

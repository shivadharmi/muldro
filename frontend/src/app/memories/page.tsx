"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { fetchMemories } from "@/lib/api";
import type { MemoryItem } from "@/lib/types";

const MEMORY_TYPES = ["all", "episodic", "semantic", "preference", "procedural"];

const typeColors: Record<string, string> = {
  episodic: "text-blue-400",
  semantic: "text-green-400",
  preference: "text-purple-400",
  procedural: "text-yellow-400",
};

export default function MemoriesPage() {
  const [selectedType, setSelectedType] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");

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

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Memories"
        subtitle="Browse the knowledge base — facts, preferences, and learned patterns"
      />

      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search memories..."
          className="flex-1 rounded-lg bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <div className="flex gap-2 flex-wrap">
          {MEMORY_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setSelectedType(t)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                selectedType === t
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-400 hover:text-white"
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
            <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-900 p-3 animate-pulse">
              <div className="h-3 w-16 bg-neutral-800 rounded mb-2" />
              <div className="h-4 w-full bg-neutral-800 rounded mb-1" />
              <div className="h-4 w-3/4 bg-neutral-800 rounded" />
              <div className="mt-2 h-1 bg-neutral-800 rounded-full" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && memories.length === 0 && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">No memories found</p>
          <p className="text-neutral-600 text-xs mt-1">
            Memories are created as Jarvis processes events and learns from interactions.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {memories.map((mem: MemoryItem, i: number) => {
          const memType = mem.memory_type || "";
          const confidence = mem.confidence ?? 1.0;
          const pct = Math.round(confidence * 100);

          return (
            <div
              key={mem.memory_id || i}
              className="rounded-lg border border-neutral-800 bg-neutral-900 p-3"
            >
              <div className="flex items-start justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-medium uppercase ${typeColors[memType] || "text-neutral-400"}`}>
                    {memType}
                  </span>
                  {mem.scope && (
                    <span className="text-[10px] text-neutral-600">{mem.scope}</span>
                  )}
                </div>
                <span className="text-[10px] text-neutral-500">{pct}%</span>
              </div>
              <p className="text-sm text-neutral-300">
                {mem.fact_text || ""}
              </p>
              <div className="mt-2 h-1 rounded-full bg-neutral-800 overflow-hidden">
                <div
                  className="h-full rounded-full bg-blue-500/50"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

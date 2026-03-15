"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { fetchMemories } from "@/lib/api";

const MEMORY_TYPES = ["all", "episodic", "semantic", "preference", "procedural"];

const typeColors: Record<string, string> = {
  episodic: "text-blue-400",
  semantic: "text-green-400",
  preference: "text-purple-400",
  procedural: "text-yellow-400",
};

export default function MemoriesPage() {
  const [selectedType, setSelectedType] = useState("all");

  const { data, isLoading } = useQuery({
    queryKey: ["memories", selectedType],
    queryFn: () =>
      fetchMemories(selectedType === "all" ? undefined : selectedType, 50),
    refetchInterval: 30_000,
  });

  const memories = data?.memories || [];

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Memories"
        subtitle="Browse the knowledge base — facts, preferences, and learned patterns"
      />

      <div className="flex gap-2">
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
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="text-center py-12 text-neutral-500 text-sm">Loading memories...</div>
      )}

      {!isLoading && memories.length === 0 && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          No memories found
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {memories.map((mem: Record<string, unknown>, i: number) => {
          const memType = (mem.memory_type as string) || "";
          const confidence = (mem.stability_score as number) ?? 1.0;
          const pct = Math.round(confidence * 100);

          return (
            <div
              key={(mem.memory_id as string) || i}
              className="rounded-lg border border-neutral-800 bg-neutral-900 p-3"
            >
              <div className="flex items-start justify-between mb-1">
                <span className={`text-[10px] font-medium uppercase ${typeColors[memType] || "text-neutral-400"}`}>
                  {memType}
                </span>
                <span className="text-[10px] text-neutral-500">{pct}%</span>
              </div>
              <p className="text-sm text-neutral-300">
                {(mem.fact_text as string) || ""}
              </p>
              {mem.source_event_ids ? (
                <p className="text-[10px] text-neutral-600 mt-1 font-mono truncate">
                  {String((mem.source_event_ids as unknown[])?.[0] ?? "")}
                </p>
              ) : null}
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

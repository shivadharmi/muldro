"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { fetchKnowledgeGraph } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { KnowledgeSearch } from "@/components/knowledge/knowledge-search";
import { GraphFilters } from "@/components/knowledge/graph-filters";
import { GraphView } from "@/components/knowledge/graph-view";
import { GraphDetailPanel } from "@/components/knowledge/graph-detail-panel";
import { MemoriesView } from "@/components/knowledge/memories-view";
import { StatsView } from "@/components/knowledge/stats-view";

// ── Constants ─────────────────────────────────────────────────────

type KnowledgeTab = "graph" | "memories" | "stats";

const TABS: { key: KnowledgeTab; label: string }[] = [
  { key: "graph", label: "Graph" },
  { key: "memories", label: "Memories" },
  { key: "stats", label: "Stats" },
];

const VALID_TABS = new Set<string>(TABS.map((t) => t.key));

function isValidTab(value: string | null): value is KnowledgeTab {
  return value !== null && VALID_TABS.has(value);
}

// ── Inner content (uses useSearchParams, must be inside Suspense) ─

function KnowledgeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeTab = useKnowledgeStore((s) => s.activeTab);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const setGraphData = useKnowledgeStore((s) => s.setGraphData);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);

  // Fetch initial graph data
  const { data: graphResponse } = useQuery({
    queryKey: ["knowledge-graph"],
    queryFn: fetchKnowledgeGraph,
  });

  // Sync graph response into store
  useEffect(() => {
    if (graphResponse) {
      setGraphData({
        nodes: graphResponse.nodes,
        edges: graphResponse.edges,
      });
    }
  }, [graphResponse, setGraphData]);

  // Sync URL params → store on mount and URL changes
  useEffect(() => {
    const tabParam = searchParams.get("tab");
    const entityParam = searchParams.get("entity");
    const memoryParam = searchParams.get("memory");

    if (entityParam) {
      setActiveTab("graph");
      selectEntity(entityParam);
    } else if (memoryParam) {
      setActiveTab("memories");
      selectMemory(memoryParam);
    } else if (isValidTab(tabParam)) {
      setActiveTab(tabParam);
    }
  }, [searchParams, setActiveTab, selectEntity, selectMemory]);

  // Handle tab change: update store + URL
  function handleTabChange(key: string) {
    if (!isValidTab(key)) return;
    setActiveTab(key);
    const params = new URLSearchParams();
    if (key !== "graph") {
      params.set("tab", key);
    }
    const qs = params.toString();
    router.replace(qs ? `/knowledge?${qs}` : "/knowledge");
  }

  // Entity and relationship counts from the graph response
  const entityCount = graphResponse?.stats?.total_entities ?? 0;
  const relationshipCount = graphResponse?.stats?.total_relationships ?? 0;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Top bar */}
      <div className="shrink-0 p-4 pb-0 border-b border-b-primary bg-surface-1">
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 mb-3">
          <PageHeader title="Knowledge" />
          <KnowledgeSearch />
          {(entityCount > 0 || relationshipCount > 0) && (
            <span className="text-xs text-t-muted whitespace-nowrap sm:ml-auto hidden sm:inline">
              {entityCount} entities &middot; {relationshipCount} relationships
            </span>
          )}
        </div>
        <Tabs tabs={TABS} active={activeTab} onChange={handleTabChange} />
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === "graph" && (
          <div className="flex flex-col h-full">
            <GraphFilters />
            <div className="flex flex-1 overflow-hidden relative">
              <div className="flex-1 relative overflow-hidden">
                <GraphView />
              </div>
              <GraphDetailPanel />
            </div>
          </div>
        )}
        {activeTab === "memories" && <MemoriesView />}
        {activeTab === "stats" && <StatsView />}
      </div>
    </div>
  );
}

// ── Page export (Suspense boundary for useSearchParams) ──────────

export default function KnowledgePage() {
  return (
    <Suspense>
      <KnowledgeContent />
    </Suspense>
  );
}

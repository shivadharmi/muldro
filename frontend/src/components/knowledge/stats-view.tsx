"use client";

import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchKnowledgeStats } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { SkeletonGrid, Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "./stat-card";
import { BarChart } from "./bar-chart";
import { DonutChart } from "./donut-chart";
import { CommunityCard } from "./community-card";

// ── CSS variable resolver (for chart libraries that need resolved colors) ──

function resolveCssVar(varExpr: string): string {
  if (typeof window === "undefined") return "#888";
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(varExpr.replace("var(", "").replace(")", ""))
      .trim() || "#888"
  );
}

// ── Entity type → chart color mapping ────────────────────────────

const ENTITY_TYPE_CHART_COLORS: Record<string, string> = {
  person: "var(--muldro-chart-1)",
  organization: "var(--muldro-chart-2)",
  project: "var(--muldro-chart-3)",
  document: "var(--muldro-chart-4)",
  repository: "var(--muldro-chart-5)",
};

const CHART_PALETTE = [
  "var(--muldro-chart-1)",
  "var(--muldro-chart-2)",
  "var(--muldro-chart-3)",
  "var(--muldro-chart-4)",
  "var(--muldro-chart-5)",
];

const DEFAULT_CHART_COLOR = "var(--muldro-text-muted)";

function getEntityChartColor(type: string): string {
  return resolveCssVar(ENTITY_TYPE_CHART_COLORS[type.toLowerCase()] ?? DEFAULT_CHART_COLOR);
}

// ── Entity type badge helper ──────────────────────────────────────

function EntityTypeBadge({ type }: { type: string }) {
  const color = getEntityChartColor(type);
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium"
      style={{
        color,
        backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
      }}
    >
      {type}
    </span>
  );
}

// ── Helpers ───────────────────────────────────────────────────────

/** Bucket entity counts: keep top N, aggregate the rest as "Other". */
function bucketEntityCounts(
  items: { entity_type: string; count: number }[] | null | undefined,
  topN: number,
): { label: string; value: number; color: string }[] {
  if (!items || !Array.isArray(items)) return [];
  const sorted = [...items].sort((a, b) => b.count - a.count);
  const top = sorted.slice(0, topN);
  const rest = sorted.slice(topN);

  const result = top.map((item) => ({
    label: item.entity_type,
    value: item.count,
    color: getEntityChartColor(item.entity_type),
  }));

  if (rest.length > 0) {
    const otherTotal = rest.reduce((sum, item) => sum + item.count, 0);
    result.push({
      label: "Other",
      value: otherTotal,
      color: DEFAULT_CHART_COLOR,
    });
  }

  return result;
}

/** Map memory type counts to donut data with cycling palette colors. */
function mapMemoryCountsToDonut(
  items: { memory_type: string; count: number }[] | null | undefined,
): { label: string; value: number; color: string }[] {
  if (!items || !Array.isArray(items)) return [];
  return items.map((item, i) => ({
    label: item.memory_type,
    value: item.count,
    color: resolveCssVar(CHART_PALETTE[i % CHART_PALETTE.length]),
  }));
}

/** Format a date string to a short label like "Mar 25". */
function formatDayLabel(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ── Stats View (main dashboard) ──────────────────────────────────

export function StatsView() {
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["knowledge-stats"],
    queryFn: fetchKnowledgeStats,
  });

  const handleEntityClick = useCallback(
    (entityId: string) => {
      selectEntity(entityId);
      setActiveTab("graph");
    },
    [selectEntity, setActiveTab],
  );

  const handleCommunityClick = useCallback(
    (seedEntityId: string) => {
      selectEntity(seedEntityId);
      setActiveTab("graph");
    },
    [selectEntity, setActiveTab],
  );

  // ── Loading / Error states ─────────────────────────────────────

  if (isLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-6">
        <SkeletonGrid count={4} />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-sm text-t-tertiary">Failed to load stats</p>
      </div>
    );
  }

  // ── Data prep ──────────────────────────────────────────────────

  const toArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);
  const entityCounts = toArray<{ entity_type: string; count: number }>(data.entity_counts_by_type);
  const memoryCounts = toArray<{ memory_type: string; count: number }>(data.memory_counts_by_type);
  const growthDays = toArray<{ date: string; entities: number; memories: number }>(data.growth_by_day);
  const centralEntities = toArray<{ entity_id: string; name: string; entity_type: string; degree: number }>(data.central_entities);
  const communities = toArray<{ seed_entity_id: string; seed_name: string; seed_type: string; community_size: number }>(data.communities);
  const staleRelationships = toArray<{ relation_id: string; from_name: string; to_name: string; relation_type: string }>(data.stale_relationships);
  const weeklyDelta = data.weekly_delta ?? { entities: 0, relationships: 0, memories: 0 };

  const entityBarData = bucketEntityCounts(entityCounts, 6);
  const memoryDonutData = mapMemoryCountsToDonut(memoryCounts);
  const memoryDonutTotal = memoryCounts.reduce(
    (sum, item) => sum + item.count,
    0,
  );

  const confidenceDisplay =
    data.avg_confidence >= 0
      ? `${Math.round(data.avg_confidence * 100)}%`
      : "N/A";

  const growthBarData = growthDays.map((day) => ({
    label: formatDayLabel(day.date),
    value: day.entities + day.memories,
    color: resolveCssVar("var(--muldro-chart-1)"),
  }));

  const topCentralEntities = centralEntities.slice(0, 5);
  const topCommunities = communities.slice(0, 4);

  // ── Render ─────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6 space-y-4">
      {/* Row 1: Metric cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="Total Entities"
          value={data.total_entities}
          delta={weeklyDelta.entities}
          color="text-j-primary"
        />
        <StatCard
          label="Total Relationships"
          value={data.total_relationships}
          delta={weeklyDelta.relationships}
          color="text-j-secondary"
        />
        <StatCard
          label="Total Memories"
          value={data.total_memories}
          delta={weeklyDelta.memories}
          color="text-j-accent"
        />
        <StatCard
          label="Avg Confidence"
          value={confidenceDisplay}
          color="text-j-warning"
        />
      </div>

      {/* Row 2: Entity types bar chart + Memory types donut */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Entity Types */}
        <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
          <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
            <span className="text-sm font-semibold text-t-primary">
              Entity Types
            </span>
            <span className="text-xs text-t-muted">
              {entityCounts.length} types
            </span>
          </div>
          <div className="px-4 py-4">
            {entityBarData.length > 0 ? (
              <BarChart data={entityBarData} height={120} />
            ) : (
              <p className="text-xs text-t-muted text-center py-8">
                No entity data
              </p>
            )}
          </div>
        </div>

        {/* Memory Types */}
        <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
          <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
            <span className="text-sm font-semibold text-t-primary">
              Memory Types
            </span>
            <span className="text-xs text-t-muted">
              {memoryCounts.length} types
            </span>
          </div>
          <div className="px-4 py-4 flex justify-center">
            {memoryDonutData.length > 0 ? (
              <DonutChart
                data={memoryDonutData}
                total={memoryDonutTotal}
                size={130}
              />
            ) : (
              <p className="text-xs text-t-muted text-center py-8">
                No memory data
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Row 3: Most Connected Entities + Communities */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Most Connected Entities */}
        <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
          <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
            <span className="text-sm font-semibold text-t-primary">
              Most Connected Entities
            </span>
          </div>
          <div className="px-4 py-4">
            {topCentralEntities.length > 0 ? (
              <div className="space-y-2">
                {topCentralEntities.map((entity, index) => (
                  <div
                    key={entity.entity_id}
                    className="flex items-center gap-3"
                  >
                    <span className="text-xs text-t-muted w-4 text-right tabular-nums">
                      {index + 1}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleEntityClick(entity.entity_id)}
                      className="text-sm text-j-primary hover:underline cursor-pointer truncate flex-1 text-left"
                    >
                      {entity.name}
                    </button>
                    <EntityTypeBadge type={entity.entity_type} />
                    <span className="text-xs text-t-muted tabular-nums shrink-0">
                      {entity.degree} connections
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-t-muted text-center py-8">
                No entity data
              </p>
            )}
          </div>
        </div>

        {/* Communities Detected */}
        <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
          <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
            <span className="text-sm font-semibold text-t-primary">
              Communities Detected
            </span>
            <span className="text-xs text-t-muted">
              {communities.length} found
            </span>
          </div>
          <div className="px-4 py-4">
            {topCommunities.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {topCommunities.map((community) => (
                  <CommunityCard
                    key={community.seed_entity_id}
                    name={community.seed_name}
                    memberCount={community.community_size}
                    seedType={community.seed_type}
                    onClick={() =>
                      handleCommunityClick(community.seed_entity_id)
                    }
                  />
                ))}
              </div>
            ) : (
              <p className="text-xs text-t-muted text-center py-8">
                No communities detected
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Row 4: Knowledge Growth */}
      <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
        <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
          <span className="text-sm font-semibold text-t-primary">
            Knowledge Growth
          </span>
          <span className="text-xs text-t-muted">Last 7 days</span>
        </div>
        <div className="px-4 py-4">
          {growthBarData.length > 0 ? (
            <>
              <BarChart data={growthBarData} height={100} />
              <p className="text-[10px] text-t-muted text-center mt-2">
                New entities + memories per day
              </p>
            </>
          ) : (
            <p className="text-xs text-t-muted text-center py-8">
              No growth data
            </p>
          )}
        </div>
      </div>

      {/* Row 5: Stale Relationships (conditional) */}
      {staleRelationships.length > 0 && (
        <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] overflow-hidden">
          <div className="px-4 py-3 border-b border-b-secondary flex items-center justify-between">
            <span className="text-sm font-semibold text-t-primary">
              Stale Relationships
            </span>
            <span className="text-xs text-j-warning">
              {staleRelationships.length} flagged
            </span>
          </div>
          <div className="px-4 py-4 space-y-1.5">
            {staleRelationships.map((rel, idx) => (
              <div
                key={`${rel.relation_id}-${idx}`}
                className="flex items-center gap-2 text-xs text-t-secondary"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-j-warning shrink-0" />
                <span className="truncate">
                  {rel.from_name}
                  <span className="text-t-muted mx-1.5">&rarr;</span>
                  <span className="text-t-muted">{rel.relation_type}</span>
                  <span className="text-t-muted mx-1.5">&rarr;</span>
                  {rel.to_name}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

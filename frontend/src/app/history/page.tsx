"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchHistory, retryRun } from "@/lib/api";
import { useHistoryStore } from "@/stores/history-store";
import type { HistoryItem } from "@/stores/history-store";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { RunRow } from "@/components/history/run-row";
import { HistoryFilters } from "@/components/history/history-filters";
import { RunDetailModal } from "@/components/history/run-detail-modal";
import type { SurfaceUpdate } from "@/lib/a2ui-types";

export default function HistoryPage() {
  const { user } = useAuth();
  const {
    items,
    total,
    offset,
    filters,
    setItems,
    appendItems,
    setOffset,
    updateRunLiveState,
  } = useHistoryStore();

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["history", filters, offset],
    queryFn: () =>
      fetchHistory({
        ...filters,
        limit: 20,
        offset,
        from: filters.dateFrom ?? undefined,
        to: filters.dateTo ?? undefined,
      }),
    refetchInterval: 30000,
  });

  // Sync query data → store
  useEffect(() => {
    if (!data) return;
    if (offset === 0) setItems(data.items as HistoryItem[], data.total);
    else appendItems(data.items as HistoryItem[], data.total);
  }, [data, offset, setItems, appendItems]);

  // WebSocket — live execution updates
  // SurfaceUpdate.steps (StepState[]) differs from HistoryStepSummary[], so we
  // forward only the phase and approval fields to updateRunLiveState.
  const handleSurfaceUpdate = useCallback(
    (update: SurfaceUpdate) => {
      updateRunLiveState(update.surface_id, {
        phase: update.phase,
        approval: update.approval
          ? {
              approval_id: update.approval.approval_id,
              step_id: null,
              step_description: update.approval.step_description,
              risk_level: update.approval.risk_level,
              trust_level: update.approval.trust_level,
            }
          : null,
      });
    },
    [updateRunLiveState]
  );

  useJarvisWs({
    userId: user?.user_id ?? "",
    onSurfaceUpdate: handleSurfaceUpdate,
    enabled: !!user,
  });

  const handleRetry = useCallback(
    async (runId: string) => {
      await retryRun(runId);
      refetch();
    },
    [refetch]
  );

  // Summary stats (computed from items)
  const activeCount = items.filter((i) =>
    ["running", "pending", "awaiting_approval"].includes(i.status)
  ).length;

  const completedToday = items.filter((i) => {
    if (i.status !== "completed" || !i.completed_at) return false;
    return (
      new Date(i.completed_at).toDateString() === new Date().toDateString()
    );
  }).length;

  const failedCount = items.filter((i) => i.status === "failed").length;

  const dailyCost = items.reduce((sum, i) => sum + (i.total_cost_usd ?? 0), 0);

  return (
    <div className="flex-1 bg-[#0d1117] min-h-screen">
      <HistoryFilters />

      {/* Summary stats bar */}
      <div className="flex gap-6 px-5 py-2.5 border-b border-[#21262d] text-xs text-[#8b949e]">
        <span>
          <span className="text-green-400">{activeCount}</span> active
        </span>
        <span>
          <span className="text-[#e6edf3]">{completedToday}</span> completed
          today
        </span>
        <span>
          <span className="text-red-400">{failedCount}</span> failed
        </span>
        <span className="ml-auto">${dailyCost.toFixed(2)} today</span>
      </div>

      {/* Timeline */}
      {isLoading && items.length === 0 ? (
        <div className="flex items-center justify-center py-20 text-[#8b949e] text-sm">
          Loading history...
        </div>
      ) : items.length === 0 ? (
        <div className="flex items-center justify-center py-20 text-[#8b949e] text-sm">
          No runs found
        </div>
      ) : (
        <>
          {items.map((item) => (
            <RunRow key={item.run_id} item={item} onRetry={handleRetry} />
          ))}
          {items.length < total && (
            <div className="py-4 text-center">
              <button
                onClick={() => setOffset(offset + 20)}
                className="text-[#58a6ff] text-sm hover:underline"
              >
                Load more runs...
              </button>
            </div>
          )}
        </>
      )}

      <RunDetailModal />
    </div>
  );
}

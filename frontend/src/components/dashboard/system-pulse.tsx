"use client";

import { useMemo } from "react";
import type { SystemDashboard } from "@/lib/types";

function computeLastObsAgo(observations: Record<string, { last_observed_at: string | null }>) {
  const obsTimes = Object.values(observations)
    .map((o) => o.last_observed_at)
    .filter(Boolean)
    .sort()
    .reverse();
  const lastObs = obsTimes[0];
  if (!lastObs) return "—";
  const diff = Date.now() - new Date(lastObs).getTime();
  const mins = Math.floor(diff / 60000);
  return mins < 60 ? `${mins}m ago` : `${Math.floor(mins / 60)}h ago`;
}

export function SystemPulse({ data }: { data: SystemDashboard | undefined }) {
  const lastObsAgo = useMemo(
    () => (data ? computeLastObsAgo(data.observations) : "—"),
    [data]
  );

  if (!data) {
    return (
      <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-4">
        <div className="skeleton h-8 w-full" />
      </div>
    );
  }

  const budget = data.budget;
  const queues = data.queues;
  const budgetPct = Math.min(budget.percent_used, 100);

  const statusColor =
    data.status === "healthy"
      ? "bg-j-success"
      : data.status === "degraded"
        ? "bg-j-warning"
        : "bg-j-error";

  const budgetBarColor =
    budgetPct > 80
      ? "bg-j-error"
      : budgetPct > 60
        ? "bg-j-warning"
        : "bg-j-primary";

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary px-5 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* Status */}
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${statusColor}`} />
          <span className="text-xs font-medium text-t-primary capitalize">
            {data.status}
          </span>
        </div>

        {/* Budget */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-t-muted">Budget</span>
          <div className="w-16 h-1.5 bg-surface-3 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${budgetBarColor}`}
              style={{ width: `${budgetPct}%` }}
            />
          </div>
          <span className="text-[10px] text-t-tertiary">
            ${budget.daily_spend_usd.toFixed(2)}/${budget.daily_limit_usd.toFixed(0)}
          </span>
        </div>

        {/* Queue counts */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-t-muted">Queue</span>
          <span className="text-[10px] text-t-tertiary">
            {queues.approvals_pending} approvals · {queues.plans_in_flight} plans
          </span>
        </div>

        {/* Last observation */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-t-muted">Last sync</span>
          <span className="text-[10px] text-t-tertiary">{lastObsAgo}</span>
        </div>
      </div>
    </div>
  );
}

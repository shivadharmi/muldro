"use client";

import type { SystemDashboard } from "@/lib/types";

interface Props {
  system: SystemDashboard | undefined;
}

export function WorkspaceStatusBar({ system }: Props) {
  const budget = system?.budget;
  const queues = system?.queues;

  return (
    <div className="rounded-xl border border-b-primary bg-surface-0 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {/* Health indicator */}
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${
              system?.status === "healthy"
                ? "bg-green-400"
                : system?.status === "degraded"
                  ? "bg-yellow-400"
                  : "bg-neutral-400"
            }`}
          />
          <span className="text-xs font-medium text-t-primary capitalize">
            {system?.status ?? "Loading"}
          </span>
        </div>

        <span className="w-px h-4 bg-b-primary hidden sm:block" />

        {/* Budget */}
        {budget && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-t-tertiary">Budget</span>
            <div className="w-20 h-1.5 bg-surface-2 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  budget.percent_used > 80
                    ? "bg-red-400"
                    : budget.percent_used > 60
                      ? "bg-yellow-400"
                      : "bg-accent-primary"
                }`}
                style={{ width: `${Math.min(budget.percent_used, 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-t-tertiary font-mono">
              ${budget.daily_spend_usd.toFixed(2)}/{budget.daily_limit_usd.toFixed(0)}
            </span>
          </div>
        )}

        <span className="w-px h-4 bg-b-primary hidden sm:block" />

        {/* Queue */}
        {queues && (
          <div className="flex items-center gap-3 text-[10px] text-t-tertiary">
            {queues.approvals_pending > 0 && (
              <span className="text-orange-400 font-medium">
                {queues.approvals_pending} approval{queues.approvals_pending !== 1 ? "s" : ""}
              </span>
            )}
            {queues.plans_in_flight > 0 && (
              <span>{queues.plans_in_flight} plan{queues.plans_in_flight !== 1 ? "s" : ""} active</span>
            )}
            {queues.approvals_pending === 0 && queues.plans_in_flight === 0 && (
              <span className="text-green-400">All clear</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

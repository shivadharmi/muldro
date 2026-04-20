"use client";

import type { SystemDashboard } from "@/lib/types";

interface Props {
  system: SystemDashboard | undefined;
}

export function WorkspaceStatusBar({ system }: Props) {
  const budget = system?.budget;
  const queues = system?.queues;

  if (!system) {
    return (
      <div className="flex gap-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex-1 h-16 rounded-[var(--radius-lg)] skeleton" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-3">
      {/* Health */}
      <div className="rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 px-4 py-3">
        <p className="text-[10px] text-t-muted font-medium uppercase tracking-wider mb-1.5">System</p>
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${
              system.status === "healthy"
                ? "bg-j-success"
                : system.status === "degraded"
                  ? "bg-j-warning"
                  : "bg-t-muted"
            }`}
          />
          <span className="text-sm font-medium text-t-primary capitalize">
            {system.status}
          </span>
        </div>
      </div>

      {/* Budget */}
      <div className="rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 px-4 py-3">
        <p className="text-[10px] text-t-muted font-medium uppercase tracking-wider mb-1.5">Daily Budget</p>
        {budget ? (
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-medium text-t-primary font-mono">
              ${budget.daily_spend_usd.toFixed(2)}
            </span>
            <div className="flex-1 h-1.5 bg-surface-3 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-300 ${
                  budget.percent_used > 80
                    ? "bg-j-error"
                    : budget.percent_used > 60
                      ? "bg-j-warning"
                      : "bg-j-primary"
                }`}
                style={{ width: `${Math.min(budget.percent_used, 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-t-muted font-mono">
              ${budget.daily_limit_usd.toFixed(0)}
            </span>
          </div>
        ) : (
          <span className="text-sm text-t-muted">--</span>
        )}
      </div>

      {/* Queue */}
      <div className="rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 px-4 py-3">
        <p className="text-[10px] text-t-muted font-medium uppercase tracking-wider mb-1.5">Queue</p>
        {queues ? (
          <div className="flex items-center gap-3 text-sm">
            {queues.approvals_pending > 0 && (
              <span className="font-medium text-j-warning">
                {queues.approvals_pending} approval{queues.approvals_pending !== 1 ? "s" : ""}
              </span>
            )}
            {queues.plans_in_flight > 0 && (
              <span className="text-t-secondary">
                {queues.plans_in_flight} active
              </span>
            )}
            {queues.approvals_pending === 0 && queues.plans_in_flight === 0 && (
              <span className="text-j-success font-medium">All clear</span>
            )}
          </div>
        ) : (
          <span className="text-sm text-t-muted">--</span>
        )}
      </div>
    </div>
  );
}

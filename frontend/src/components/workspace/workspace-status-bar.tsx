"use client";

import type { SystemDashboard } from "@/lib/types";

const EYEBROW =
  "text-[10px] uppercase tracking-[0.08em] text-t-muted font-medium";

interface HealthMeta {
  dot: string;
  label: string;
  tone: string;
}

function healthMeta(status: string | undefined): HealthMeta {
  switch (status) {
    case "healthy":
      return { dot: "bg-j-success", label: "Nominal", tone: "text-j-success" };
    case "degraded":
      return {
        dot: "bg-j-warning",
        label: "Degraded",
        tone: "text-j-warning",
      };
    case "unhealthy":
      return { dot: "bg-j-error", label: "Unhealthy", tone: "text-j-error" };
    default:
      return { dot: "bg-t-muted", label: "Unknown", tone: "text-t-muted" };
  }
}

function budgetBarColor(percentUsed: number): string {
  if (percentUsed > 80) return "bg-j-error";
  if (percentUsed > 60) return "bg-j-warning";
  return "bg-j-primary";
}

function Separator() {
  return <div className="hidden sm:block w-px self-stretch bg-b-secondary" />;
}

interface WorkspaceStatusBarProps {
  system: SystemDashboard | undefined;
  activeAgents: string[];
}

export function WorkspaceStatusBar({
  system,
  activeAgents,
}: WorkspaceStatusBarProps) {
  if (!system) {
    return (
      <div className="h-14 rounded-[var(--radius-lg)] skeleton border border-b-secondary" />
    );
  }

  const health = healthMeta(system.status);
  const budget = system.budget;
  const queues = system.queues;

  const percentUsed = budget ? Math.min(budget.percent_used, 100) : 0;
  const queueCount = queues
    ? queues.approvals_pending + queues.plans_in_flight
    : 0;

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 px-5 py-3.5">
      {/* Health */}
      <div className="flex flex-col gap-1 shrink-0">
        <span className={EYEBROW}>Health</span>
        <span className={`flex items-center gap-1.5 text-sm ${health.tone}`}>
          <span
            className={`w-1.5 h-1.5 rounded-full animate-pulse-live ${health.dot}`}
          />
          {health.label}
        </span>
      </div>

      <Separator />

      {/* Daily Budget */}
      <div className="flex flex-col gap-1 flex-1 min-w-[180px]">
        <span className={EYEBROW}>Daily Budget</span>
        <div className="flex items-center gap-2.5">
          <span className="text-sm tabular-nums">
            {budget ? (
              <>
                <span className="text-t-primary">
                  ${budget.daily_spend_usd.toFixed(2)}
                </span>
                <span className="text-t-muted">
                  {" "}
                  / ${budget.daily_limit_usd.toFixed(2)}
                </span>
              </>
            ) : (
              <span className="text-t-muted">--</span>
            )}
          </span>
          <span className="flex-1 max-w-[160px] h-1 bg-surface-3 rounded-full overflow-hidden">
            <span
              className={`block h-full rounded-full transition-all duration-300 ${budgetBarColor(percentUsed)}`}
              style={{ width: `${percentUsed}%` }}
            />
          </span>
        </div>
      </div>

      <Separator />

      {/* Queue */}
      <div className="flex flex-col gap-1 shrink-0">
        <span className={EYEBROW}>Queue</span>
        <span className="text-sm text-t-primary tabular-nums">
          {queues ? (
            queueCount === 0 ? (
              <span className="text-j-success">All clear</span>
            ) : (
              `${queueCount} task${queueCount !== 1 ? "s" : ""}`
            )
          ) : (
            <span className="text-t-muted">--</span>
          )}
        </span>
      </div>

      <Separator />

      {/* Active Agents */}
      <div className="flex flex-col gap-1 shrink-0 sm:max-w-[40%]">
        <span className={EYEBROW}>Active Agents</span>
        {activeAgents.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {activeAgents.map((agent) => (
              <span
                key={agent}
                className="px-2 py-0.5 rounded-full bg-surface-2 border border-b-secondary text-[11px] text-t-secondary lowercase font-mono"
              >
                {agent}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-sm text-t-muted">idle</span>
        )}
      </div>
    </div>
  );
}

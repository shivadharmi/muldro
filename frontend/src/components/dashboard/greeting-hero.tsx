"use client";

import Link from "next/link";
import type { SystemDashboard } from "@/lib/types";

export function GreetingHero({
  headline,
  approvalCount,
  sourceCount,
  system,
}: {
  headline: string | null;
  approvalCount: number;
  sourceCount: number;
  system: SystemDashboard | undefined;
}) {
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="rounded-[var(--radius-xl)] p-6 sm:p-8 bg-surface-1 border border-b-secondary relative overflow-hidden noise-bg">
      {/* Atmospheric gradient — stronger presence */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 10% 40%, hsl(193 100% 62% / 0.06), transparent 60%), radial-gradient(ellipse 60% 40% at 90% 80%, hsl(247 80% 72% / 0.04), transparent 50%)",
        }}
      />
      <div className="relative flex flex-col md:flex-row md:items-start md:justify-between gap-6">
        <div className="flex-1 min-w-0">
          <p className="text-[11px] text-t-muted font-medium tracking-wide uppercase mb-2">{today}</p>
          <h2 className="text-2xl sm:text-3xl font-semibold text-t-primary mb-1 tracking-tight">{greeting}</h2>
          {headline && (
            <p className="text-sm text-t-secondary mb-5 max-w-xl leading-relaxed">{headline}</p>
          )}
          {!headline && <div className="mb-5" />}

          <div className="flex flex-wrap items-center gap-3">
            <Link
              href="/chat"
              className="inline-flex items-center gap-2 px-4 py-2 bg-j-primary text-j-primary-fg text-[13px] font-medium rounded-[var(--radius-md)] hover:bg-j-primary-hover transition-all duration-150 shadow-[var(--shadow-sm)]"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                <path d="M3 3h10v7H6l-3 3V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
              </svg>
              Talk to Jarvis
            </Link>
            {approvalCount > 0 && (
              <Link
                href="/settings"
                className="inline-flex items-center gap-1.5 px-3 py-2 bg-j-warning-soft text-j-warning text-[13px] font-medium rounded-[var(--radius-md)] hover:bg-j-warning/20 transition-colors"
              >
                <span className="w-2 h-2 rounded-full bg-j-warning animate-pulse-live" />
                {approvalCount} pending
              </Link>
            )}

            <div className="flex items-center gap-1.5 text-[11px] text-t-muted ml-1">
              <span className="w-1.5 h-1.5 rounded-full bg-j-success animate-pulse-live" />
              {sourceCount > 0
                ? `Monitoring ${sourceCount} source${sourceCount !== 1 ? "s" : ""}`
                : "No sources connected"}
            </div>
          </div>
        </div>

        <StatusPanel system={system} />
      </div>
    </div>
  );
}

function StatusPanel({ system }: { system: SystemDashboard | undefined }) {
  if (!system) {
    return <div className="w-full md:w-56 h-20 rounded-[var(--radius-md)] skeleton shrink-0" />;
  }

  const budget = system.budget;
  const queues = system.queues;

  const statusDot =
    system.status === "healthy"
      ? "bg-j-success"
      : system.status === "degraded"
        ? "bg-j-warning"
        : "bg-t-muted";

  const budgetBar =
    budget && budget.percent_used > 80
      ? "bg-j-error"
      : budget && budget.percent_used > 60
        ? "bg-j-warning"
        : "bg-j-primary";

  const queueText =
    queues && queues.approvals_pending === 0 && queues.plans_in_flight === 0
      ? "All clear"
      : null;

  return (
    <div className="flex flex-col gap-2 text-xs w-full md:w-56 shrink-0 md:items-end">
      {/* System */}
      <div className="flex items-center justify-between gap-3 w-full">
        <span className="text-t-muted">System</span>
        <span className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${statusDot}`} />
          <span className="capitalize text-t-primary">{system.status}</span>
        </span>
      </div>

      {/* Budget */}
      <div className="flex items-center justify-between gap-3 w-full">
        <span className="text-t-muted">Budget</span>
        {budget ? (
          <span className="flex items-center gap-2">
            <span className="text-t-primary font-mono">
              ${budget.daily_spend_usd.toFixed(2)}
            </span>
            <span className="text-t-muted font-mono">
              / ${budget.daily_limit_usd.toFixed(0)}
            </span>
            <span className="w-16 h-1 bg-surface-3 rounded-full overflow-hidden">
              <span
                className={`block h-full rounded-full transition-all duration-300 ${budgetBar}`}
                style={{ width: `${Math.min(budget.percent_used, 100)}%` }}
              />
            </span>
          </span>
        ) : (
          <span className="text-t-muted">--</span>
        )}
      </div>

      {/* Queue */}
      <div className="flex items-center justify-between gap-3 w-full">
        <span className="text-t-muted">Queue</span>
        {queues ? (
          queueText ? (
            <span className="text-j-success">{queueText}</span>
          ) : (
            <span className="flex items-center gap-2">
              {queues.approvals_pending > 0 && (
                <span className="text-j-warning">
                  {queues.approvals_pending} approval
                  {queues.approvals_pending !== 1 ? "s" : ""}
                </span>
              )}
              {queues.plans_in_flight > 0 && (
                <span className="text-t-primary">
                  {queues.plans_in_flight} active
                </span>
              )}
            </span>
          )
        ) : (
          <span className="text-t-muted">--</span>
        )}
      </div>
    </div>
  );
}

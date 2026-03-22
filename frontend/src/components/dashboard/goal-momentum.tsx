"use client";

import Link from "next/link";
import type { DashboardGoal } from "@/lib/types";

function priorityColor(p: string) {
  switch (p) {
    case "high":
      return "bg-j-warning";
    case "medium":
      return "bg-j-primary";
    case "low":
      return "bg-j-success";
    default:
      return "bg-j-primary";
  }
}

export function GoalMomentum({ goals }: { goals: DashboardGoal[] }) {
  if (goals.length === 0) {
    return (
      <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-t-primary">Goal Momentum</h3>
          <Link href="/goals" className="text-[11px] text-j-primary hover:underline">
            Create goal
          </Link>
        </div>
        <div className="flex items-center gap-3 py-3">
          <div className="w-8 h-8 rounded-full bg-surface-2 flex items-center justify-center shrink-0">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-t-tertiary">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5"/>
              <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5"/>
            </svg>
          </div>
          <div>
            <p className="text-xs text-t-secondary">No active goals yet</p>
            <p className="text-[10px] text-t-tertiary">Set goals to track progress on what matters most</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-secondary p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-t-primary">Goal Momentum</h3>
        <Link href="/goals" className="text-[11px] text-j-primary hover:underline">
          All goals
        </Link>
      </div>
      <div className="space-y-4">
        {goals.map((g) => {
          const pct = Math.round(g.progress * 100);
          return (
            <Link key={g.goal_id} href="/goals" className="block group">
              <div className="flex items-center justify-between mb-1">
                <p className="text-xs font-medium text-t-primary group-hover:text-j-primary transition-colors truncate">
                  {g.title}
                </p>
                <span className="text-[10px] text-t-muted ml-2 shrink-0">
                  {g.completed_task_count}/{g.task_count} tasks
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2.5 bg-surface-3 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${priorityColor(g.priority)}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-[10px] font-semibold text-t-secondary w-8 text-right">
                  {pct}%
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

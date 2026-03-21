"use client";

import type { RuntimeRun } from "@/lib/types/runtime";

interface Props {
  runs: RuntimeRun[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

const statusColors: Record<string, string> = {
  running: "bg-accent-primary",
  pending: "bg-surface-2",
  awaiting_approval: "bg-status-warning",
  paused: "bg-status-warning",
  completed: "bg-status-success",
  failed: "bg-status-error",
  cancelled: "bg-surface-2",
  blocked: "bg-status-error",
};

export function WorkflowRunList({ runs, selectedRunId, onSelect }: Props) {
  if (runs.length === 0) {
    return (
      <div className="p-4 text-sm text-t-tertiary">No active runs.</div>
    );
  }

  return (
    <ul className="divide-y divide-b-primary">
      {runs.map((run) => (
        <li key={run.run_id}>
          <button
            onClick={() => onSelect(run.run_id)}
            className={`w-full text-left p-3 hover:bg-surface-1 transition-colors cursor-pointer ${
              selectedRunId === run.run_id ? "bg-surface-1" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${statusColors[run.status] ?? "bg-surface-2"}`} />
              <span className="text-sm font-medium text-t-primary truncate">
                {run.run_id.slice(0, 20)}...
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1 text-xs text-t-tertiary">
              <span className="capitalize">{run.status}</span>
              <span>{run.completed_steps}/{run.total_steps} steps</span>
              <span>{run.progress_pct}%</span>
            </div>
            {/* Progress bar */}
            <div className="mt-1.5 h-1 bg-surface-2 rounded-full overflow-hidden">
              <div
                className="h-full bg-accent-primary rounded-full transition-all"
                style={{ width: `${run.progress_pct}%` }}
              />
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

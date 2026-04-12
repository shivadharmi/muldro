"use client";

import type { StepState } from "@/lib/a2ui-types";

interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
}

const statusIcon: Record<string, { icon: string; className: string }> = {
  pending: { icon: "○", className: "text-t-tertiary" },
  executing: { icon: "◉", className: "text-blue-400 animate-pulse" },
  completed: { icon: "✓", className: "text-green-400" },
  failed: { icon: "✗", className: "text-red-400" },
  approval_needed: { icon: "⚠", className: "text-amber-400" },
  user_action: { icon: "👤", className: "text-purple-400" },
};

export function StepList({ steps, currentStep }: StepListProps) {
  return (
    <div className="space-y-1">
      {steps.map((step) => {
        const isCurrent = step.step_id === currentStep;
        const { icon, className } = statusIcon[step.status] ?? statusIcon.pending;

        return (
          <div
            key={step.step_id}
            className={`flex items-start gap-2 py-1.5 px-2 rounded text-sm ${
              isCurrent ? "bg-surface-1" : ""
            }`}
          >
            <span className={`shrink-0 w-5 text-center ${className}`}>{icon}</span>
            <div className="flex-1 min-w-0">
              <span className={`${isCurrent ? "text-t-primary font-medium" : "text-t-secondary"}`}>
                {step.description}
              </span>
              {step.output_summary && step.status === "completed" && (
                <p className="text-xs text-t-tertiary mt-0.5 line-clamp-2">
                  {step.output_summary}
                </p>
              )}
              {step.status === "failed" && step.output_summary && (
                <p className="text-xs text-red-400 mt-0.5 line-clamp-2">
                  {step.output_summary}
                </p>
              )}
            </div>
            {step.duration_ms != null && step.status === "completed" && (
              <span className="text-[10px] text-t-tertiary shrink-0">
                {formatDuration(step.duration_ms)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Compact step list for surface card preview (shows counts, not full list). */
export function StepListCompact({ steps }: { steps: StepState[] }) {
  const completed = steps.filter((s) => s.status === "completed").length;
  const failed = steps.filter((s) => s.status === "failed").length;
  const total = steps.length;

  return (
    <div className="flex items-center gap-2 text-xs text-t-tertiary">
      <span>{completed}/{total} steps</span>
      {failed > 0 && <span className="text-red-400">{failed} failed</span>}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

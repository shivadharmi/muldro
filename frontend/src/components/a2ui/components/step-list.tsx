"use client";

import { useState } from "react";
import type { StepState } from "@/lib/a2ui-types";
import { statusTextColor } from "@/lib/design-tokens";

interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
}

const statusIcon: Record<string, { icon: string; className: string }> = {
  pending: { icon: "○", className: "text-t-tertiary" },
  executing: { icon: "◉", className: `${statusTextColor("executing")} animate-pulse` },
  completed: { icon: "✓", className: statusTextColor("completed") },
  failed: { icon: "✗", className: statusTextColor("failed") },
  approval_needed: { icon: "⚠", className: statusTextColor("awaiting_approval") },
  user_action: { icon: "👤", className: statusTextColor("user_action") },
};

export function StepList({ steps, currentStep }: StepListProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const toggleExpand = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  return (
    <div className="space-y-1.5">
      {steps.map((step) => {
        const isCurrent = step.step_id === currentStep;
        const { icon, className } = statusIcon[step.status] ?? statusIcon.pending;
        const isExpanded = expandedSteps.has(step.step_id);
        const hasLongOutput = (step.output_summary?.length ?? 0) > 120;

        return (
          <div
            key={step.step_id}
            className={`flex items-start gap-2 text-sm ${
              isCurrent
                ? "bg-j-primary-soft border-l-2 border-l-j-primary py-2 px-3 rounded-[var(--radius-sm)]"
                : "py-1.5 px-2"
            }`}
          >
            <span className={`shrink-0 w-5 text-center ${className}`}>{icon}</span>
            <div className="flex-1 min-w-0">
              <span className={isCurrent ? "text-t-primary font-medium" : "text-t-secondary"}>
                {step.description}
              </span>
              {step.output_summary && step.status === "completed" && (
                <div className="mt-0.5">
                  <p className={`text-xs text-t-tertiary ${!isExpanded && hasLongOutput ? "line-clamp-2" : ""}`}>
                    {step.output_summary}
                  </p>
                  {hasLongOutput && (
                    <button
                      type="button"
                      onClick={() => toggleExpand(step.step_id)}
                      className="text-[11px] text-j-primary cursor-pointer hover:underline mt-0.5"
                    >
                      {isExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
              {step.status === "failed" && step.output_summary && (
                <div className="mt-0.5">
                  <p className={`text-xs text-j-error ${!isExpanded && hasLongOutput ? "line-clamp-2" : ""}`}>
                    {step.output_summary}
                  </p>
                  {hasLongOutput && (
                    <button
                      type="button"
                      onClick={() => toggleExpand(step.step_id)}
                      className="text-[11px] text-j-primary cursor-pointer hover:underline mt-0.5"
                    >
                      {isExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
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
      {failed > 0 && <span className="text-j-error font-medium">{failed} failed</span>}
      {total > 0 && (
        <div className="w-12 h-1 bg-surface-3 rounded-full overflow-hidden ml-1">
          <div
            className={`h-full rounded-full transition-all duration-300 ${failed > 0 ? "bg-j-error" : "bg-j-success"}`}
            style={{ width: `${(completed / total) * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

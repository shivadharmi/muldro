"use client";

import { useState, useEffect, useMemo } from "react";
import type { StepState } from "@/lib/a2ui-types";
import { statusTextColor } from "@/lib/design-tokens";
import { stepStatusIcon, formatDuration } from "./step-presentation";

interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
  triggeringStepId?: string | null;
}

function useElapsedTimer(startedAt: string | null, active: boolean): number {
  const [elapsedMs, setElapsedMs] = useState(() =>
    active && startedAt ? Date.now() - new Date(startedAt).getTime() : 0,
  );

  useEffect(() => {
    if (!active || !startedAt) return;
    const start = new Date(startedAt).getTime();
    const tick = () => setElapsedMs(Date.now() - start);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt, active]);

  return active ? elapsedMs : 0;
}

function ElapsedBadge({ step }: { step: StepState }) {
  const isExecuting = step.status === "executing";
  const isFailed = step.status === "failed";
  const elapsedMs = useElapsedTimer(step.started_at ?? null, isExecuting);

  if (isExecuting && step.started_at) {
    return (
      <span
        className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-j-primary/12 text-j-primary text-[11px] shrink-0"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        <span className="w-[5px] h-[5px] rounded-full bg-j-primary animate-pulse-live" />
        {formatDuration(elapsedMs)}
      </span>
    );
  }

  if (isFailed && step.duration_ms != null) {
    return (
      <span
        className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-j-error/12 text-j-error text-[11px] shrink-0"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {formatDuration(step.duration_ms)}
      </span>
    );
  }

  return null;
}

function CompletedGroupSummary({
  count,
  totalDurationMs,
  onExpand,
}: {
  count: number;
  totalDurationMs: number;
  onExpand: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onExpand}
      className="flex items-center gap-2 w-full px-3 py-2 rounded-[var(--radius-md)] bg-j-success-soft border border-j-success/12 cursor-pointer hover:bg-j-success/10 transition-colors"
    >
      <span className={`shrink-0 w-5 text-center ${statusTextColor("completed")}`}>✓</span>
      <span className="flex-1 text-left text-xs">
        <span className="text-t-secondary font-medium">{count} steps completed</span>
        {totalDurationMs > 0 && (
          <span className="ml-1.5 text-t-tertiary">{formatDuration(totalDurationMs)} total</span>
        )}
      </span>
      <span className="text-[10px] text-t-tertiary px-1.5 py-0.5 bg-surface-2 rounded-[var(--radius-sm)]">
        ▸ Expand
      </span>
    </button>
  );
}

export function StepList({ steps, currentStep, triggeringStepId }: StepListProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [showCompletedSteps, setShowCompletedSteps] = useState(false);

  const toggleExpand = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  const completedSteps = useMemo(() => steps.filter((s) => s.status === "completed"), [steps]);
  const shouldGroup = completedSteps.length >= 5 && !showCompletedSteps;
  const totalCompletedMs = useMemo(
    () => completedSteps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0),
    [completedSteps],
  );
  const firstCompletedIdx = useMemo(
    () => (shouldGroup ? steps.findIndex((s) => s.status === "completed") : -1),
    [steps, shouldGroup],
  );

  return (
    <div className="space-y-1.5">
      {showCompletedSteps && completedSteps.length >= 5 && (
        <button
          type="button"
          onClick={() => setShowCompletedSteps(false)}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-[var(--radius-md)] bg-j-success-soft border border-j-success/12 cursor-pointer hover:bg-j-success/10 transition-colors"
        >
          <span className={`shrink-0 w-5 text-center ${statusTextColor("completed")}`}>✓</span>
          <span className="flex-1 text-left text-xs text-t-secondary font-medium">
            {completedSteps.length} steps completed
          </span>
          <span className="text-[10px] text-t-tertiary px-1.5 py-0.5 bg-surface-2 rounded-[var(--radius-sm)]">
            ▾ Collapse
          </span>
        </button>
      )}
      {steps.map((step, idx) => {
        if (step.status === "completed" && shouldGroup) {
          if (idx === firstCompletedIdx) {
            return (
              <CompletedGroupSummary
                key="__completed_group"
                count={completedSteps.length}
                totalDurationMs={totalCompletedMs}
                onExpand={() => setShowCompletedSteps(true)}
              />
            );
          }
          return null;
        }

        const isCurrent = step.step_id === currentStep;
        const isTriggering = step.step_id === triggeringStepId;
        const { icon, className } = stepStatusIcon(step.status);
        const isExpanded = expandedSteps.has(step.step_id);
        const hasLongOutput = (step.output_summary?.length ?? 0) > 120;

        return (
          <div
            key={step.step_id}
            className={`flex items-start gap-2 text-sm ${
              isTriggering
                ? "bg-j-warning-soft border-l-2 border-l-j-warning py-2 px-3 rounded-[var(--radius-sm)]"
                : isCurrent
                  ? "bg-j-primary-soft border-l-2 border-l-j-primary py-2 px-3 rounded-[var(--radius-sm)]"
                  : "py-1.5 px-2"
            }`}
          >
            <span className={`shrink-0 w-5 text-center ${className}`}>{icon}</span>
            <div className="flex-1 min-w-0">
              <span className={isCurrent ? "text-t-primary font-medium" : "text-t-secondary"}>
                {step.description}
              </span>
              {isTriggering && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-j-warning/12 text-j-warning uppercase font-medium shrink-0 inline-block mt-0.5">
                  awaiting
                </span>
              )}
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
            {step.status === "completed" && step.duration_ms != null && (
              <span className="text-[10px] text-t-tertiary shrink-0">
                {formatDuration(step.duration_ms)}
              </span>
            )}
            {(step.status === "executing" || step.status === "failed") && (
              <ElapsedBadge step={step} />
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

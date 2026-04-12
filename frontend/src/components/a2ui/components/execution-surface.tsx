"use client";

import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary } from "@/lib/a2ui-types";
import { phaseTextColor } from "@/lib/design-tokens";
import { StepList } from "./step-list";
import { InlineApprovalCard } from "./inline-approval";

interface Props {
  component: A2UIComponent;
}

/** Extract execution fields from component properties (set by backend or surface store merge). */
function getExecutionProps(properties: Record<string, unknown>) {
  return {
    goal: (properties.goal as string) ?? "Executing...",
    phase: (properties.phase as ExecutionPhase) ?? "planning",
    steps: (properties.steps as StepState[]) ?? [],
    currentStep: (properties.current_step as string) ?? null,
    progress: (properties.progress as string) ?? "",
    approval: (properties.approval as ApprovalContext) ?? null,
    results: (properties.results as ResultSummary) ?? null,
  };
}

const phaseLabel: Record<string, string> = {
  planning: "Planning",
  plan_ready: "Plan Ready",
  executing: "Executing",
  approval_needed: "Approval Needed",
  completed: "Completed",
  failed: "Failed",
  partial: "Partially Completed",
};

export function A2UIExecutionSurface({ component }: Props) {
  const { goal, phase, steps, currentStep, approval, results, progress } =
    getExecutionProps(component.properties);

  const completedCount = steps.filter((s) => s.status === "completed").length;
  const totalCount = steps.length;
  const progressPct = totalCount > 0 ? completedCount / totalCount : 0;
  const labelText = phaseLabel[phase] ?? "Planning";
  const { className: phaseClass } = phaseTextColor(phase);

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-t-primary">{goal}</h3>
        <span className={`text-xs font-medium ${phaseClass}`}>{labelText}</span>
      </div>

      {/* Planning spinner */}
      {phase === "planning" && (
        <div className="flex items-center gap-2 py-4 justify-center">
          <div className="w-4 h-4 border-2 border-j-info/30 border-t-j-info rounded-full animate-spin" />
          <span className="text-xs text-t-tertiary">Analyzing and building plan...</span>
        </div>
      )}

      {/* Step list (shown for all phases except planning) */}
      {phase !== "planning" && steps.length > 0 && (
        <StepList steps={steps} currentStep={currentStep} />
      )}

      {/* Inline approval card */}
      {phase === "approval_needed" && approval && (
        <InlineApprovalCard approval={approval} />
      )}

      {/* Results summary */}
      {phase === "completed" && results && (
        <div className="space-y-2 rounded-[var(--radius-lg)] bg-j-success-soft border border-j-success/20 p-3">
          {results.key_findings.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Key Findings</p>
              <ul className="space-y-0.5">
                {results.key_findings.map((f, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-j-success shrink-0">-</span>
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {results.artifacts_created.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Artifacts</p>
              <div className="flex flex-wrap gap-1">
                {results.artifacts_created.map((a, i) => (
                  <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary">
                    {a}
                  </span>
                ))}
              </div>
            </div>
          )}
          {results.suggested_next.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Suggested Next</p>
              <ul className="space-y-0.5">
                {results.suggested_next.map((s, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-j-info shrink-0">→</span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Failure context */}
      {phase === "failed" && (
        <div className="rounded-[var(--radius-lg)] bg-j-error-soft border border-j-error/20 p-3">
          <p className="text-xs font-medium text-j-error mb-1">Execution Failed</p>
          {steps.filter((s) => s.status === "failed").map((s) => (
            <p key={s.step_id} className="text-xs text-t-tertiary">
              <span className="text-j-error">✗</span> {s.description}
              {s.output_summary && `: ${s.output_summary}`}
            </p>
          ))}
        </div>
      )}

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="space-y-1">
          <div className="w-full h-1.5 bg-surface-2 rounded-full">
            <div
              className={`h-full rounded-full transition-all ${
                phase === "failed" ? "bg-j-error" : phase === "completed" ? "bg-j-success" : "bg-j-info"
              }`}
              style={{ width: `${Math.min(progressPct * 100, 100)}%` }}
            />
          </div>
          {progress && (
            <p className="text-[10px] text-t-tertiary">{progress}</p>
          )}
        </div>
      )}
    </div>
  );
}

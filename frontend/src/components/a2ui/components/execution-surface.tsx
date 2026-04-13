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
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-t-primary">{goal}</h3>
        <span className={`text-xs font-medium transition-colors duration-200 ${phaseClass}`}>{labelText}</span>
      </div>

      {/* Planning spinner */}
      {phase === "planning" && (
        <div className="animate-fade-in flex flex-col items-center gap-2 py-6">
          <div className="w-4 h-4 border-2 border-j-info/30 border-t-j-info rounded-full animate-spin" />
          <span className="text-xs text-t-tertiary">Analyzing and building plan...</span>
          <span className="text-[11px] text-t-muted">This usually takes a few seconds</span>
        </div>
      )}

      {/* Step list (shown for all phases except planning) */}
      {phase !== "planning" && steps.length > 0 && (
        <div key={`steps-${phase}`} className="animate-slide-in-up">
          <StepList steps={steps} currentStep={currentStep} triggeringStepId={approval?.triggering_step_id ?? null} />
        </div>
      )}

      {/* Inline approval card */}
      {phase === "approval_needed" && approval && (
        <>
          {approval.triggering_step_id && (
            <div className="ml-5 w-px h-2 bg-j-warning/30" />
          )}
          <div className="animate-slide-in-up">
            <InlineApprovalCard approval={approval} />
          </div>
        </>
      )}

      {/* Results summary */}
      {phase === "completed" && results && (
        <div className="animate-fade-in rounded-[var(--radius-lg)] bg-j-success-soft border border-j-success/20 p-4">
          {results.key_findings.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Key Findings</p>
              <ul className="space-y-1">
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
            <div className={results.key_findings.length > 0 ? "border-t border-b-secondary pt-3 mt-3" : ""}>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Artifacts</p>
              <div className="flex flex-wrap gap-1.5">
                {results.artifacts_created.map((a, i) => (
                  <span key={i} className="text-[11px] px-2 py-1 rounded-[var(--radius-md)] bg-surface-2 text-t-secondary">
                    {a}
                  </span>
                ))}
              </div>
            </div>
          )}
          {results.suggested_next.length > 0 && (
            <div className={results.key_findings.length > 0 || results.artifacts_created.length > 0 ? "border-t border-b-secondary pt-3 mt-3" : ""}>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Suggested Next</p>
              <ul className="space-y-1">
                {results.suggested_next.map((s, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-j-info shrink-0">&rarr;</span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Failure context — show full step list for context, then error box */}
      {phase === "failed" && (
        <div className="animate-fade-in">
          {steps.length > 0 && (
            <StepList steps={steps} currentStep={currentStep} triggeringStepId={approval?.triggering_step_id ?? null} />
          )}
          <div className="rounded-[var(--radius-lg)] bg-j-error-soft border border-j-error/20 p-4">
            <p className="text-sm font-semibold text-j-error mb-2">Execution Failed</p>
            {steps.filter((s) => s.status === "failed").map((s) => (
              <p key={s.step_id} className="text-xs text-t-secondary">
                <span className="text-j-error">&#10007;</span> {s.description}
                {s.output_summary && `: ${s.output_summary}`}
              </p>
            ))}
          </div>
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
            <p className="text-[11px] text-t-tertiary">{progress}</p>
          )}
        </div>
      )}
    </div>
  );
}

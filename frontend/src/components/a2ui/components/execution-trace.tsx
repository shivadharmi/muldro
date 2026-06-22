import type { A2UIComponent } from "@/lib/a2ui-types";
import { statusColor } from "@/lib/design-tokens";
import { stepStatusIcon, formatDuration } from "./step-presentation";

interface Props {
  component: A2UIComponent;
}

// ExecutionTrace renders a distinct *timeline* (connector line + status dots),
// not a flat step list — that's why it isn't folded into StepList. It still
// sources its status glyph/color and duration formatting from the shared
// step-presentation helpers so it stays visually consistent with the other two
// step surfaces.

export function A2UIExecutionTrace({ component }: Props) {
  const steps = (component.properties.steps as Array<Record<string, unknown>>) || [];

  return (
    <div className="space-y-0">
      {steps.map((step, i) => {
        const status = (step.status as string) || "pending";
        const { icon } = stepStatusIcon(status);
        const durationMs = typeof step.duration_ms === "number" ? step.duration_ms : null;
        return (
          <div key={i} className="flex gap-3 relative">
            <div className="flex flex-col items-center">
              <div
                className={`w-6 h-6 rounded-full ${statusColor(status)} flex items-center justify-center text-j-primary-fg text-xs z-10`}
              >
                {icon}
              </div>
              {i < steps.length - 1 && <div className="w-px flex-1 bg-surface-3" />}
            </div>
            <div className="pb-4 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-t-primary">
                  {(step.title as string) || (step.task_type as string) || `Step ${i + 1}`}
                </p>
                <span className="text-[10px] text-t-tertiary">{status}</span>
              </div>
              {step.description ? (
                <p className="text-xs text-t-secondary mt-0.5">{String(step.description)}</p>
              ) : null}
              {step.error ? (
                <p className="text-xs text-j-error mt-0.5">{String(step.error)}</p>
              ) : null}
              {durationMs != null ? (
                <p className="text-[10px] text-t-muted mt-0.5">{formatDuration(durationMs)}</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

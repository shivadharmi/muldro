"use client";

export type ExecutionStage =
  | "planned"
  | "queued"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed";

interface TimelineStep {
  stage: ExecutionStage;
  label: string;
  detail?: string;
  timestamp?: string;
}

interface Props {
  steps: TimelineStep[];
  currentStage?: ExecutionStage;
}

const STAGE_ORDER: ExecutionStage[] = [
  "planned",
  "queued",
  "running",
  "waiting_approval",
  "completed",
];

function stageColor(stage: ExecutionStage, isCurrent: boolean): string {
  if (stage === "failed") return "bg-status-error";
  if (stage === "completed") return "bg-status-success";
  if (stage === "waiting_approval") return "bg-status-warning";
  if (isCurrent) return "bg-accent-primary";
  return "bg-surface-2";
}

export function ExecutionTimeline({ steps, currentStage }: Props) {
  return (
    <div className="space-y-0">
      {steps.map((step, i) => {
        const isCurrent = step.stage === currentStage;
        const isPast =
          currentStage &&
          STAGE_ORDER.indexOf(step.stage) <
            STAGE_ORDER.indexOf(currentStage);

        return (
          <div key={i} className="flex items-start gap-3">
            {/* Dot + connector */}
            <div className="flex flex-col items-center">
              <div
                className={`w-3 h-3 rounded-full border-2 ${
                  isPast || isCurrent
                    ? stageColor(step.stage, isCurrent)
                    : "bg-surface-1 border-b-secondary"
                } ${isCurrent ? "ring-2 ring-accent-primary/30" : ""}`}
              />
              {i < steps.length - 1 && (
                <div
                  className={`w-0.5 h-6 ${
                    isPast ? "bg-accent-primary/50" : "bg-surface-2"
                  }`}
                />
              )}
            </div>

            {/* Label */}
            <div className="pb-4">
              <p
                className={`text-sm ${
                  isCurrent
                    ? "text-t-primary font-medium"
                    : isPast
                      ? "text-t-secondary"
                      : "text-t-tertiary"
                }`}
              >
                {step.label}
              </p>
              {step.detail && (
                <p className="text-xs text-t-tertiary mt-0.5">{step.detail}</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

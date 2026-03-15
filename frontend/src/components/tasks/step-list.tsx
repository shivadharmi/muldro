import type { TaskStep } from "@/lib/types";
import { Badge, statusVariant } from "@/components/ui/badge";

export function StepList({ steps }: { steps: TaskStep[] }) {
  if (steps.length === 0) {
    return <p className="text-xs text-neutral-600">No steps defined</p>;
  }

  return (
    <div className="space-y-2">
      {steps.map((step, i) => (
        <div
          key={`${step.task_id}-${i}`}
          className="flex items-start gap-3 p-3 bg-neutral-800/50 rounded-lg"
        >
          <div className="flex-shrink-0 w-6 h-6 rounded-full bg-neutral-700 flex items-center justify-center text-xs font-medium">
            {i + 1}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{step.task_type}</span>
              <Badge variant={statusVariant(step.status)}>{step.status}</Badge>
            </div>
            {step.result_summary && (
              <p className="text-xs text-neutral-400 mt-1">{step.result_summary}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

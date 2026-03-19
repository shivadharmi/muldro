import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const statusIcons: Record<string, { color: string; symbol: string }> = {
  completed: { color: "bg-j-success", symbol: "\u2713" },
  running: { color: "bg-j-primary animate-pulse", symbol: "\u25B6" },
  failed: { color: "bg-j-error", symbol: "\u2717" },
  pending: { color: "bg-surface-3", symbol: "\u25CB" },
  skipped: { color: "bg-surface-4", symbol: "\u2014" },
};

export function A2UIExecutionTrace({ component }: Props) {
  const steps = (component.properties.steps as Array<Record<string, unknown>>) || [];

  return (
    <div className="space-y-0">
      {steps.map((step, i) => {
        const status = (step.status as string) || "pending";
        const icon = statusIcons[status] || statusIcons.pending;
        return (
          <div key={i} className="flex gap-3 relative">
            <div className="flex flex-col items-center">
              <div className={`w-6 h-6 rounded-full ${icon.color} flex items-center justify-center text-j-primary-fg text-xs z-10`}>
                {icon.symbol}
              </div>
              {i < steps.length - 1 && (
                <div className="w-px flex-1 bg-surface-3" />
              )}
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
              {step.duration_ms ? (
                <p className="text-[10px] text-t-muted mt-0.5">{String(step.duration_ms)}ms</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

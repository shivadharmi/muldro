import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const statusIcons: Record<string, { color: string; symbol: string }> = {
  completed: { color: "bg-green-500", symbol: "\u2713" },
  running: { color: "bg-blue-500 animate-pulse", symbol: "\u25B6" },
  failed: { color: "bg-red-500", symbol: "\u2717" },
  pending: { color: "bg-neutral-600", symbol: "\u25CB" },
  skipped: { color: "bg-neutral-700", symbol: "\u2014" },
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
              <div className={`w-6 h-6 rounded-full ${icon.color} flex items-center justify-center text-white text-xs z-10`}>
                {icon.symbol}
              </div>
              {i < steps.length - 1 && (
                <div className="w-px flex-1 bg-neutral-700" />
              )}
            </div>
            <div className="pb-4 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-white">
                  {(step.title as string) || (step.task_type as string) || `Step ${i + 1}`}
                </p>
                <span className="text-[10px] text-neutral-500">{status}</span>
              </div>
              {step.description ? (
                <p className="text-xs text-neutral-400 mt-0.5">{String(step.description)}</p>
              ) : null}
              {step.error ? (
                <p className="text-xs text-red-400 mt-0.5">{String(step.error)}</p>
              ) : null}
              {step.duration_ms ? (
                <p className="text-[10px] text-neutral-600 mt-0.5">{String(step.duration_ms)}ms</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

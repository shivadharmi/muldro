"use client";

import type { RunStep } from "@/lib/types";
import { Badge, statusVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { useState } from "react";

export function StepTimeline({ steps }: { steps: RunStep[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (steps.length === 0) {
    return <p className="text-xs text-neutral-600">No steps yet</p>;
  }

  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-px bg-neutral-800" />

      <div className="space-y-3">
        {steps.map((step) => {
          const isExpanded = expandedId === step.step_id;
          const dotColor =
            step.status === "completed"
              ? "bg-green-500"
              : step.status === "running"
                ? "bg-blue-500 animate-pulse"
                : step.status === "failed"
                  ? "bg-red-500"
                  : step.status === "skipped"
                    ? "bg-neutral-600"
                    : "bg-neutral-700";

          return (
            <div key={step.step_id} className="relative pl-6">
              <div className={`absolute left-0 top-1.5 w-[15px] h-[15px] rounded-full border-2 border-neutral-900 ${dotColor}`} />

              <button
                onClick={() => setExpandedId(isExpanded ? null : step.step_id)}
                className="w-full text-left"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-sm text-white">
                      {step.name || step.step_type || step.step_id}
                    </span>
                    {step.step_type && step.name && (
                      <span className="text-xs text-neutral-600 ml-2">{step.step_type}</span>
                    )}
                  </div>
                  <Badge variant={statusVariant(step.status)}>{step.status}</Badge>
                </div>

                <div className="flex items-center gap-3 text-[10px] text-neutral-600 mt-0.5">
                  {step.started_at && <TimeAgo date={step.started_at} />}
                  {step.depends_on && step.depends_on.length > 0 && (
                    <span>depends on: {step.depends_on.length}</span>
                  )}
                </div>
              </button>

              {isExpanded && (
                <div className="mt-2 rounded bg-neutral-800/50 p-3 space-y-2">
                  {step.input_data && (
                    <div>
                      <p className="text-[10px] text-neutral-500 uppercase mb-0.5">Input</p>
                      <pre className="text-xs text-neutral-400 font-mono overflow-x-auto max-h-32 overflow-y-auto">
                        {JSON.stringify(step.input_data, null, 2)}
                      </pre>
                    </div>
                  )}
                  {step.output_data && (
                    <div>
                      <p className="text-[10px] text-neutral-500 uppercase mb-0.5">Output</p>
                      <pre className="text-xs text-neutral-400 font-mono overflow-x-auto max-h-32 overflow-y-auto">
                        {JSON.stringify(step.output_data, null, 2)}
                      </pre>
                    </div>
                  )}
                  {step.error && (
                    <div>
                      <p className="text-[10px] text-red-500 uppercase mb-0.5">Error</p>
                      <pre className="text-xs text-red-400 font-mono overflow-x-auto">
                        {JSON.stringify(step.error, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchRunSteps } from "@/lib/api";
import {
  ExecutionTimeline,
  type ExecutionStage,
} from "@/components/primitives/execution-timeline";
import type { RunStep } from "@/lib/types";

function mapStatus(status: string): ExecutionStage {
  const map: Record<string, ExecutionStage> = {
    pending: "planned",
    ready: "queued",
    running: "running",
    awaiting_approval: "waiting_approval",
    blocked: "waiting_approval",
    completed: "completed",
    failed: "failed",
    skipped: "completed",
    cancelled: "completed",
  };
  return map[status] ?? "planned";
}

function getCurrentStage(steps: RunStep[]): ExecutionStage | undefined {
  const running = steps.find((s) => s.status === "running");
  if (running) return "running";
  const waiting = steps.find((s) => s.status === "awaiting_approval" || s.status === "blocked");
  if (waiting) return "waiting_approval";
  const allDone = steps.every((s) => ["completed", "failed", "skipped", "cancelled"].includes(s.status));
  if (allDone && steps.length > 0) return "completed";
  return "queued";
}

interface Props {
  runId: string;
}

export function WorkflowRunDetail({ runId }: Props) {
  const { data: steps, isLoading } = useQuery({
    queryKey: ["run-steps", runId],
    queryFn: () => fetchRunSteps(runId),
    refetchInterval: 5_000,
  });

  if (isLoading) {
    return <div className="p-4 text-sm text-t-tertiary">Loading run details...</div>;
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-t-primary">Run Detail</h3>
        <span className="text-xs text-t-tertiary font-mono">{runId.slice(0, 24)}</span>
      </div>

      <ExecutionTimeline
        steps={(steps ?? []).map((s) => ({
          stage: mapStatus(s.status),
          label: s.name ?? s.step_type ?? s.step_id,
          detail: s.error ? String(Object.values(s.error)[0] ?? "") : undefined,
          timestamp: s.started_at ?? undefined,
        }))}
        currentStage={getCurrentStage(steps ?? [])}
      />
    </div>
  );
}

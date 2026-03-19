"use client";

import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchRun, fetchRunSteps, fetchRunTrace, fetchRunArtifacts, resumeRun } from "@/lib/api";
import { useRunSSE } from "@/hooks/use-sse";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeAgo } from "@/components/ui/time-ago";
import { StepTimeline } from "@/components/runs/step-timeline";
import { TracePanel } from "@/components/runs/trace-panel";
import { useCallback } from "react";
import type { TraceDetail } from "@/lib/types";
import Link from "next/link";

export default function RunViewerPage() {
  const params = useParams();
  const runId = params.runId as string;
  const queryClient = useQueryClient();

  const { data: run, isLoading: runLoading } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => fetchRun(runId),
  });

  const { data: steps = [] } = useQuery({
    queryKey: ["run-steps", runId],
    queryFn: () => fetchRunSteps(runId),
  });

  const { data: trace } = useQuery({
    queryKey: ["run-trace", runId],
    queryFn: () => fetchRunTrace(runId),
  });

  const { data: artifacts = [] } = useQuery({
    queryKey: ["run-artifacts", runId],
    queryFn: () => fetchRunArtifacts(runId),
  });

  const resumeMut = useMutation({
    mutationFn: () => resumeRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["run-steps", runId] });
    },
  });

  const handleSSEEvent = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["run", runId] });
    queryClient.invalidateQueries({ queryKey: ["run-steps", runId] });
  }, [queryClient, runId]);

  const isLive = run?.status === "running" || run?.status === "pending";
  useRunSSE(isLive ? runId : null, handleSSEEvent);

  if (runLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-4">
        <PageHeader title="Run Viewer" />
        <div className="animate-pulse space-y-3">
          <div className="h-6 w-48 bg-surface-2 rounded" />
          <div className="h-4 w-32 bg-surface-2 rounded" />
        </div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="p-4 sm:p-6">
        <PageHeader title="Run Viewer" />
        <p className="text-t-tertiary text-sm">Run not found</p>
      </div>
    );
  }

  const canResume = run.status === "paused" || run.status === "awaiting_approval";
  const durationMs =
    run.started_at && run.completed_at
      ? new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()
      : null;

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <PageHeader title="Run Viewer" />
          <div className="flex items-center gap-3 mt-1">
            <span className="text-xs text-t-tertiary font-mono">{run.run_id}</span>
            <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
          </div>
          <div className="flex items-center gap-4 text-xs text-t-tertiary mt-1">
            <span>Plan: {run.plan_id}</span>
            {durationMs != null && <span>{(durationMs / 1000).toFixed(1)}s</span>}
            {run.started_at && <TimeAgo date={run.started_at} />}
            {run.retry_count > 0 && <span>Retries: {run.retry_count}</span>}
          </div>
        </div>
        {canResume && (
          <Button
            onClick={() => resumeMut.mutate()}
            disabled={resumeMut.isPending}
          >
            {resumeMut.isPending ? "Resuming..." : "Resume Run"}
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Steps Timeline - 2 columns */}
        <div className="col-span-2 space-y-4">
          <Card>
            <CardHeader>
              <span className="text-sm font-medium">
                Steps ({steps.length})
                {isLive && <span className="ml-2 text-j-primary text-xs animate-pulse">Live</span>}
              </span>
            </CardHeader>
            <CardBody>
              <StepTimeline steps={steps} />
            </CardBody>
          </Card>

          {run.error && (
            <Card>
              <CardHeader>
                <span className="text-sm font-medium text-j-error">Error</span>
              </CardHeader>
              <CardBody>
                <pre className="text-xs text-j-error font-mono overflow-x-auto">
                  {JSON.stringify(run.error, null, 2)}
                </pre>
              </CardBody>
            </Card>
          )}

          {artifacts.length > 0 && (
            <Card>
              <CardHeader>
                <span className="text-sm font-medium">Artifacts ({artifacts.length})</span>
              </CardHeader>
              <CardBody className="space-y-2">
                {artifacts.map((a) => (
                  <div key={a.artifact_id} className="flex items-center justify-between py-1 border-b border-b-secondary last:border-0">
                    <div>
                      <p className="text-sm text-t-primary">{a.title}</p>
                      <p className="text-xs text-t-tertiary">{a.artifact_type}</p>
                    </div>
                    <Link href={`/artifacts/${a.artifact_id}`} className="text-xs text-j-primary">
                      View
                    </Link>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
        </div>

        {/* Trace Panel - 1 column */}
        <div>
          <Card>
            <CardHeader>
              <span className="text-sm font-medium">Trace</span>
            </CardHeader>
            <CardBody>
              <TracePanel trace={(trace as TraceDetail | undefined) ?? null} />
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

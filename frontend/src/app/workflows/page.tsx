"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchWorkflows, startWorkflow, fetchRuntimeRuns } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs } from "@/components/ui/tabs";
import { WorkflowRunList } from "@/components/feature/workflows/workflow-run-list";
import { WorkflowRunDetail } from "@/components/feature/workflows/workflow-run-detail";
import type { Workflow } from "@/lib/types";

const VIEW_TABS = [
  { key: "templates", label: "Templates" },
  { key: "runs", label: "Active Runs" },
];

function WorkflowCard({
  workflow,
  onStart,
  isStarting,
}: {
  workflow: Workflow;
  onStart: (name: string) => void;
  isStarting: boolean;
}) {
  return (
    <Card>
      <CardBody>
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <p className="text-sm font-medium">{workflow.name}</p>
            <p className="text-xs text-t-tertiary mt-1">{workflow.description}</p>
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
              <Badge variant="default">{workflow.step_count} steps</Badge>
              {workflow.tags.map((tag, i) => (
                <Badge key={i} variant="default">{tag}</Badge>
              ))}
            </div>
          </div>
          <button
            onClick={() => onStart(workflow.name)}
            className="bg-j-primary hover:bg-j-primary-hover disabled:opacity-50 text-j-primary-fg text-xs px-3 py-1.5 rounded transition-colors"
            disabled={isStarting}
          >
            {isStarting ? "Starting..." : "Start"}
          </button>
        </div>
      </CardBody>
    </Card>
  );
}

export default function WorkflowsPage() {
  const [view, setView] = useState("templates");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const { data: workflows = [], isLoading: wfLoading } = useQuery({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
    refetchInterval: 60_000,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ["runtime-runs"],
    queryFn: () => fetchRuntimeRuns(50),
    refetchInterval: 5_000,
    enabled: view === "runs",
  });

  const startMut = useMutation({
    mutationFn: ({ name, params }: { name: string; params?: Record<string, unknown> }) =>
      startWorkflow(name, params),
  });

  const [lastResult, setLastResult] = useState<{ name: string; success: boolean; message: string } | null>(null);

  function handleStart(name: string) {
    startMut.mutate(
      { name },
      {
        onSuccess: () => {
          setLastResult({ name, success: true, message: `Workflow "${name}" started` });
          setView("runs");
        },
        onError: (err) => setLastResult({ name, success: false, message: err.message }),
      }
    );
  }

  return (
    <div className="flex h-full">
      {/* Left panel */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-4 border-b border-b-primary">
          <PageHeader title="Workflows" subtitle="Templates and active runs" />
          <div className="mt-3">
            <Tabs tabs={VIEW_TABS} active={view} onChange={setView} />
          </div>
        </div>

        {lastResult && (
          <div className={`mx-4 mt-3 px-4 py-2 rounded text-sm ${
            lastResult.success
              ? "bg-j-success/10 border border-j-success text-j-success"
              : "bg-j-error/10 border border-j-error text-j-error"
          }`}>
            {lastResult.message}
            <button onClick={() => setLastResult(null)} className="ml-3 text-xs opacity-60 hover:opacity-100">
              dismiss
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4">
          {view === "templates" && (
            <>
              {wfLoading ? (
                <p className="text-t-tertiary text-sm">Loading...</p>
              ) : workflows.length === 0 ? (
                <EmptyState title="No workflows" description="No workflows registered yet" />
              ) : (
                <div className="space-y-3">
                  {workflows.map((wf) => (
                    <WorkflowCard
                      key={wf.name}
                      workflow={wf}
                      onStart={handleStart}
                      isStarting={startMut.isPending}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {view === "runs" && (
            <WorkflowRunList
              runs={runs}
              selectedRunId={selectedRunId}
              onSelect={setSelectedRunId}
            />
          )}
        </div>
      </div>

      {/* Right detail panel */}
      {view === "runs" && selectedRunId && (
        <div className="w-[400px] shrink-0 border-l border-b-primary overflow-y-auto hidden lg:block">
          <WorkflowRunDetail runId={selectedRunId} />
        </div>
      )}
    </div>
  );
}

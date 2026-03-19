"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchWorkflows, startWorkflow } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Workflow } from "@/lib/types";

function WorkflowCard({
  workflow,
  onStart,
  isStarting,
}: {
  workflow: Workflow;
  onStart: (name: string) => void;
  isStarting: boolean;
}) {
  const [showParams, setShowParams] = useState(false);
  const [paramsJson, setParamsJson] = useState("{}");

  function handleStart() {
    try {
      JSON.parse(paramsJson);
    } catch {
      // invalid JSON — ignore params
    }
    onStart(workflow.name);
    setShowParams(false);
    setParamsJson("{}");
  }

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
          <div className="ml-3 flex flex-col items-end gap-2">
            <button
              onClick={() => setShowParams(!showParams)}
              className="bg-j-primary hover:bg-j-primary-hover disabled:opacity-50 text-j-primary-fg text-xs px-3 py-1.5 rounded transition-colors"
              disabled={isStarting}
            >
              {isStarting ? "Starting..." : "Start"}
            </button>
          </div>
        </div>
        {showParams && (
          <div className="mt-3 pt-3 border-t border-b-primary space-y-2">
            <label className="block text-xs text-t-secondary">Parameters (JSON)</label>
            <textarea
              value={paramsJson}
              onChange={(e) => setParamsJson(e.target.value)}
              rows={3}
              className="w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-xs text-t-primary font-mono resize-none"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowParams(false)}
                className="text-t-secondary hover:text-t-primary text-xs px-3 py-1 rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleStart}
                disabled={isStarting}
                className="bg-j-primary hover:bg-j-primary-hover disabled:opacity-50 text-j-primary-fg text-xs px-3 py-1.5 rounded transition-colors"
              >
                Start with Params
              </button>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export default function WorkflowsPage() {
  const { data: workflows = [], isLoading } = useQuery({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
    refetchInterval: 60_000,
  });

  const startMut = useMutation({
    mutationFn: ({ name, params }: { name: string; params?: Record<string, unknown> }) =>
      startWorkflow(name, params),
  });

  const [lastResult, setLastResult] = useState<{ name: string; success: boolean; message: string } | null>(null);

  function handleStart(name: string, params?: Record<string, unknown>) {
    startMut.mutate(
      { name, params },
      {
        onSuccess: () => setLastResult({ name, success: true, message: `Workflow "${name}" started successfully` }),
        onError: (err) => setLastResult({ name, success: false, message: err.message }),
      }
    );
  }

  return (
    <div className="p-4 sm:p-6">
      <PageHeader title="Workflows" subtitle="Available workflows and launcher" />

      {lastResult && (
        <div
          className={`mb-4 px-4 py-2 rounded text-sm ${
            lastResult.success
              ? "bg-j-success/10 border border-j-success text-j-success"
              : "bg-j-error/10 border border-j-error text-j-error"
          }`}
        >
          {lastResult.message}
          <button
            onClick={() => setLastResult(null)}
            className="ml-3 text-xs opacity-60 hover:opacity-100"
          >
            dismiss
          </button>
        </div>
      )}

      {isLoading ? (
        <p className="text-t-tertiary text-sm">Loading...</p>
      ) : workflows.length === 0 ? (
        <EmptyState title="No workflows" description="No workflows are registered yet" />
      ) : (
        <div className="space-y-3">
          {workflows.map((wf) => (
            <WorkflowCard
              key={wf.name}
              workflow={wf}
              onStart={(name) => handleStart(name)}
              isStarting={startMut.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

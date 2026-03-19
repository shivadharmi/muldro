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
            <p className="text-xs text-neutral-500 mt-1">{workflow.description}</p>
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
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded transition-colors"
              disabled={isStarting}
            >
              {isStarting ? "Starting..." : "Start"}
            </button>
          </div>
        </div>
        {showParams && (
          <div className="mt-3 pt-3 border-t border-neutral-800 space-y-2">
            <label className="block text-xs text-neutral-400">Parameters (JSON)</label>
            <textarea
              value={paramsJson}
              onChange={(e) => setParamsJson(e.target.value)}
              rows={3}
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-xs text-neutral-200 font-mono resize-none"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowParams(false)}
                className="text-neutral-400 hover:text-neutral-200 text-xs px-3 py-1 rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleStart}
                disabled={isStarting}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded transition-colors"
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
    <div className="p-6">
      <PageHeader title="Workflows" subtitle="Available workflows and launcher" />

      {lastResult && (
        <div
          className={`mb-4 px-4 py-2 rounded text-sm ${
            lastResult.success
              ? "bg-green-900/30 border border-green-800 text-green-400"
              : "bg-red-900/30 border border-red-800 text-red-400"
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
        <p className="text-neutral-500 text-sm">Loading...</p>
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

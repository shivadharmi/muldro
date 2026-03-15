"use client";

import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { TimeAgo } from "@/components/ui/time-ago";
import { fetchExecutions } from "@/lib/api";

const statusColors: Record<string, "default" | "blue" | "green" | "yellow" | "purple"> = {
  executing: "blue",
  completed: "green",
  failed: "default",
  approved: "purple",
  pending_approval: "yellow",
};

export default function ExecutionsPage() {
  const { data: executions, isLoading } = useQuery({
    queryKey: ["executions"],
    queryFn: fetchExecutions,
    refetchInterval: 10_000,
  });

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Executions"
        subtitle="Track plan executions and their step-by-step progress"
      />

      {isLoading && (
        <div className="text-center py-12 text-neutral-500 text-sm">Loading...</div>
      )}

      {!isLoading && (!executions || executions.length === 0) && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          No executions yet
        </div>
      )}

      <div className="space-y-3">
        {(executions || []).map((exec: Record<string, unknown>) => {
          const status = (exec.status as string) || "unknown";
          const tasks = (exec.task_runs as Array<Record<string, unknown>>) || [];
          const completedTasks = tasks.filter(
            (t) => t.status === "completed"
          ).length;

          return (
            <Card key={(exec.execution_id as string) || ""}>
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-sm font-medium text-white">
                      {(exec.execution_id as string) || ""}
                    </p>
                    <p className="text-xs text-neutral-500">
                      Plan: {(exec.plan_id as string) || "N/A"}
                    </p>
                  </div>
                  <Badge variant={statusColors[status] || "default"}>
                    {status}
                  </Badge>
                </div>

                {tasks.length > 0 && (
                  <div className="mt-3">
                    <div className="flex justify-between text-xs text-neutral-500 mb-1">
                      <span>Progress</span>
                      <span>
                        {completedTasks}/{tasks.length} steps
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-blue-500 transition-all"
                        style={{
                          width: `${tasks.length > 0 ? (completedTasks / tasks.length) * 100 : 0}%`,
                        }}
                      />
                    </div>
                  </div>
                )}

                <div className="mt-3 space-y-1">
                  {tasks.slice(0, 5).map((task, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 text-xs"
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${
                          task.status === "completed"
                            ? "bg-green-500"
                            : task.status === "failed"
                              ? "bg-red-500"
                              : task.status === "running"
                                ? "bg-blue-500 animate-pulse"
                                : "bg-neutral-600"
                        }`}
                      />
                      <span className="text-neutral-400">
                        {(task.task_type as string) || `Step ${i + 1}`}
                      </span>
                      <span className="text-neutral-600">
                        {(task.status as string) || "pending"}
                      </span>
                    </div>
                  ))}
                  {tasks.length > 5 && (
                    <p className="text-[10px] text-neutral-600">
                      +{tasks.length - 5} more steps
                    </p>
                  )}
                </div>

                {exec.started_at ? (
                  <p className="text-[10px] text-neutral-600 mt-2">
                    Started <TimeAgo date={String(exec.started_at)} />
                  </p>
                ) : null}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

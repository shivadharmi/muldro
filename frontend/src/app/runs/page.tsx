"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchExecutions } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { EmptyState } from "@/components/ui/empty-state";
import Link from "next/link";

export default function RunsListPage() {
  const { data: executions, isLoading } = useQuery({
    queryKey: ["runs-list"],
    queryFn: () => fetchExecutions({ limit: 100 }),
    refetchInterval: 10_000,
  });

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Runs"
        subtitle="View execution runs and their step-by-step progress"
        variant="monitor"
      />

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Card key={i}>
              <CardBody>
                <div className="animate-pulse space-y-2">
                  <div className="h-4 w-48 bg-surface-2 rounded" />
                  <div className="h-3 w-32 bg-surface-2 rounded" />
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {!isLoading && (!executions || executions.length === 0) && (
        <EmptyState
          title="No runs yet"
          description="Runs appear when Jarvis executes approved plans. Try sending a command in Chat."
        />
      )}

      <div className="space-y-2">
        {(executions || []).map((exec) => {
          const isActive = exec.status === "executing" || exec.status === "approved";
          return (
            <Link key={exec.execution_id} href={`/runs/${exec.execution_id}`} className="block">
              <Card className="hover:border-b-primary transition-colors">
                <CardBody>
                  <div className="flex items-center justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-t-primary truncate">
                          {exec.goal
                            ? String(exec.goal)
                            : `Run ...${exec.execution_id.slice(-8)}`}
                        </span>
                        <Badge variant={statusVariant(exec.status)}>
                          {exec.status}
                        </Badge>
                        {exec.priority && (
                          <span className="text-[10px] text-t-muted">
                            {String(exec.priority)}
                          </span>
                        )}
                        {isActive && (
                          <span className="w-1.5 h-1.5 rounded-full bg-j-primary animate-pulse" />
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-t-tertiary">
                        <span className="font-mono text-[10px]">...{exec.execution_id.slice(-8)}</span>
                        {exec.plan_id && <span>Plan: ...{exec.plan_id.slice(-8)}</span>}
                        <span>Source: {exec.source}</span>
                        {exec.execution_mode && <span>Mode: {exec.execution_mode}</span>}
                        {exec.created_at && <TimeAgo date={exec.created_at} />}
                      </div>
                    </div>
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                      fill="none"
                      className="text-t-muted shrink-0 ml-2"
                    >
                      <path
                        d="M6 4l4 4-4 4"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                  {exec.error && (
                    <p className="text-xs text-j-error mt-1.5 truncate">
                      {typeof exec.error === "object" && exec.error !== null
                        ? (exec.error as Record<string, unknown>).message
                          ? String((exec.error as Record<string, unknown>).message)
                          : JSON.stringify(exec.error)
                        : String(exec.error)}
                    </p>
                  )}
                </CardBody>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

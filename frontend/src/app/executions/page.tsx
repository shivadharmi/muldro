"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Card, CardBody } from "@/components/ui/card";
import { TimeAgo } from "@/components/ui/time-ago";
import { fetchExecutions } from "@/lib/api";
import Link from "next/link";

const STATUS_OPTIONS = ["all", "executing", "completed", "failed", "approved", "pending_approval"];
const SOURCE_OPTIONS = ["all", "plan", "chat", "schedule", "trigger"];

export default function ExecutionsPage() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");

  const { data: executions, isLoading } = useQuery({
    queryKey: ["executions", statusFilter, sourceFilter],
    queryFn: () =>
      fetchExecutions({
        status: statusFilter === "all" ? undefined : statusFilter,
        source: sourceFilter === "all" ? undefined : sourceFilter,
      }),
    refetchInterval: 10_000,
  });

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Executions"
        subtitle="Track plan executions and their progress"
      />

      <div className="flex gap-3 flex-wrap">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded bg-neutral-800 border border-neutral-700 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All statuses" : s.replace("_", " ")}
            </option>
          ))}
        </select>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="rounded bg-neutral-800 border border-neutral-700 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {SOURCE_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All sources" : s}
            </option>
          ))}
        </select>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardBody>
                <div className="animate-pulse space-y-2">
                  <div className="h-4 w-48 bg-neutral-800 rounded" />
                  <div className="h-3 w-32 bg-neutral-800 rounded" />
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {!isLoading && (!executions || executions.length === 0) && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">No executions yet</p>
          <p className="text-neutral-600 text-xs mt-1">
            Executions appear when Jarvis runs approved plans.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {(executions || []).map((exec) => (
          <Link
            key={exec.execution_id}
            href={`/runs/${exec.execution_id}`}
            className="block"
          >
            <Card className="hover:border-neutral-700 transition-colors">
              <CardBody>
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-sm font-medium text-white font-mono">
                      {exec.execution_id}
                    </p>
                    <p className="text-xs text-neutral-500 mt-0.5">
                      Plan: {exec.plan_id || "N/A"}
                    </p>
                  </div>
                  <Badge variant={statusVariant(exec.status)}>
                    {exec.status}
                  </Badge>
                </div>

                <div className="flex items-center gap-4 text-xs text-neutral-400 mt-2">
                  <span>Source: {exec.source}</span>
                  {exec.execution_mode && (
                    <span>Mode: {exec.execution_mode}</span>
                  )}
                  {exec.current_step_ids && exec.current_step_ids.length > 0 && (
                    <span>{exec.current_step_ids.length} active step(s)</span>
                  )}
                </div>

                {exec.error && (
                  <p className="text-xs text-red-400 mt-2 truncate">
                    Error: {typeof exec.error === "object" ? JSON.stringify(exec.error) : String(exec.error)}
                  </p>
                )}

                {exec.created_at && (
                  <p className="text-[10px] text-neutral-600 mt-2">
                    Created <TimeAgo date={exec.created_at} />
                  </p>
                )}
              </CardBody>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

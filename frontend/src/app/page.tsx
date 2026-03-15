"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchSystemDashboard, fetchCanvasDashboard } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { OverviewStats } from "@/components/dashboard/overview-stats";
import { ObservationTable } from "@/components/dashboard/observation-table";
import { AgentUsageTable } from "@/components/dashboard/agent-usage-table";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import Link from "next/link";

export default function DashboardPage() {
  const { data: system, isLoading: sysLoading } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: canvas } = useQuery({
    queryKey: ["canvas-dashboard"],
    queryFn: fetchCanvasDashboard,
    refetchInterval: 30_000,
  });

  if (sysLoading) {
    return (
      <div className="p-6">
        <PageHeader title="Dashboard" />
        <p className="text-neutral-500 text-sm">Loading...</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <PageHeader title="Dashboard" subtitle="System overview" />

      <OverviewStats budget={system?.budget} queues={system?.queues} />

      <div className="grid grid-cols-2 gap-4">
        <ObservationTable observations={system?.observations} />
        <AgentUsageTable agents={system?.agents} />
      </div>

      {/* Recent Activity */}
      <div className="grid grid-cols-2 gap-4">
        {/* Recent Approvals */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Recent Approvals</span>
              <Link href="/approvals" className="text-xs text-blue-400 hover:text-blue-300">
                View all
              </Link>
            </div>
          </CardHeader>
          <CardBody className="space-y-2">
            {(canvas?.pending_approvals ?? []).length === 0 ? (
              <p className="text-xs text-neutral-600">No pending approvals</p>
            ) : (
              canvas!.pending_approvals.slice(0, 5).map((a) => (
                <div
                  key={a.approval_id}
                  className="flex items-center justify-between py-1.5 border-b border-neutral-800/50 last:border-0"
                >
                  <div>
                    <p className="text-sm">{a.title}</p>
                    <TimeAgo date={a.created_at} className="text-xs" />
                  </div>
                  <Badge variant={statusVariant(a.risk_level)}>{a.risk_level}</Badge>
                </div>
              ))
            )}
          </CardBody>
        </Card>

        {/* Active Tasks */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Active Tasks</span>
              <Link href="/tasks" className="text-xs text-blue-400 hover:text-blue-300">
                View all
              </Link>
            </div>
          </CardHeader>
          <CardBody className="space-y-2">
            {(canvas?.active_tasks ?? []).length === 0 ? (
              <p className="text-xs text-neutral-600">No active tasks</p>
            ) : (
              canvas!.active_tasks.slice(0, 5).map((t) => (
                <Link
                  key={t.task_id}
                  href={`/tasks/${t.task_id}`}
                  className="flex items-center justify-between py-1.5 border-b border-neutral-800/50 last:border-0 hover:bg-neutral-800/30 -mx-1 px-1 rounded"
                >
                  <div>
                    <p className="text-sm">{t.goal}</p>
                    <TimeAgo date={t.created_at} className="text-xs" />
                  </div>
                  <Badge variant={priorityVariant(t.priority)}>{t.priority}</Badge>
                </Link>
              ))
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchSystemDashboard, fetchCanvasDashboard, generateMeetingPrep } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { OverviewStats } from "@/components/dashboard/overview-stats";
import { ObservationTable } from "@/components/dashboard/observation-table";
import { AgentUsageTable } from "@/components/dashboard/agent-usage-table";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeAgo } from "@/components/ui/time-ago";
import { Modal } from "@/components/ui/modal";
import { MeetingPrepView } from "@/components/meetings/meeting-prep-modal";
import type { MeetingPrep } from "@/lib/types";
import Link from "next/link";

export default function DashboardPage() {
  const [meetingPrep, setMeetingPrep] = useState<MeetingPrep | null>(null);

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

  const prepMut = useMutation({
    mutationFn: (meetingId?: string) => generateMeetingPrep(meetingId),
    onSuccess: (data) => setMeetingPrep(data),
  });

  if (sysLoading) {
    return (
      <div className="p-6 space-y-6">
        <PageHeader title="Dashboard" />
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-900 p-4 animate-pulse">
              <div className="h-3 w-16 bg-neutral-800 rounded mb-2" />
              <div className="h-6 w-24 bg-neutral-800 rounded" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <PageHeader title="Dashboard" subtitle="System overview" />

      {canvas?.headline && (
        <div className="rounded-lg border border-blue-900/50 bg-blue-950/30 px-4 py-3">
          <p className="text-sm text-blue-200">{canvas.headline}</p>
          {canvas.briefing_id && (
            <Link
              href={`/briefings?id=${canvas.briefing_id}`}
              className="text-xs text-blue-400 hover:text-blue-300 mt-1 inline-block"
            >
              View today&apos;s briefing
            </Link>
          )}
        </div>
      )}

      <OverviewStats budget={system?.budget} queues={system?.queues} />

      {/* Recommended Actions + Upcoming Meetings */}
      {((canvas?.recommended_actions ?? []).length > 0 ||
        (canvas?.upcoming_meetings ?? []).length > 0) && (
        <div className="grid grid-cols-2 gap-4">
          {(canvas?.recommended_actions ?? []).length > 0 && (
            <Card>
              <CardHeader>
                <span className="text-sm font-medium">Recommended Actions</span>
              </CardHeader>
              <CardBody className="space-y-1.5">
                {canvas!.recommended_actions.map((action, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-neutral-300">
                    <span className="text-blue-400 mt-0.5 shrink-0">&#x2022;</span>
                    <span>{action}</span>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}

          {(canvas?.upcoming_meetings ?? []).length > 0 && (
            <Card>
              <CardHeader>
                <span className="text-sm font-medium">Upcoming Meetings</span>
              </CardHeader>
              <CardBody className="space-y-2">
                {canvas!.upcoming_meetings.map((m) => (
                  <div
                    key={m.event_id}
                    className="flex items-center justify-between py-1.5 border-b border-neutral-800/50 last:border-0"
                  >
                    <div>
                      <p className="text-sm">{m.title}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        {m.starts_at && (
                          <span className="text-[10px] text-neutral-500">
                            {new Date(m.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        )}
                        <span className="text-[10px] text-neutral-600">
                          {m.attendee_count} attendee{m.attendee_count !== 1 ? "s" : ""}
                        </span>
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => prepMut.mutate(m.event_id)}
                      disabled={prepMut.isPending}
                    >
                      Prep
                    </Button>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
        </div>
      )}

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

      <Modal
        open={!!meetingPrep}
        onClose={() => setMeetingPrep(null)}
        title="Meeting Prep"
      >
        {meetingPrep && <MeetingPrepView prep={meetingPrep} />}
      </Modal>
    </div>
  );
}

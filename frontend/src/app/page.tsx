"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchCanvasDashboard,
  fetchNotifications,
} from "@/lib/api";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { PriorityInbox } from "@/components/dashboard/priority-inbox";
import { JarvisActivity } from "@/components/dashboard/jarvis-activity";
import { GoalMomentum } from "@/components/dashboard/goal-momentum";
import { SystemPulse } from "@/components/dashboard/system-pulse";

export default function DashboardPage() {
  const { data: system } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: canvas } = useQuery({
    queryKey: ["canvas-dashboard"],
    queryFn: fetchCanvasDashboard,
    refetchInterval: 30_000,
  });

  const { data: notifications } = useQuery({
    queryKey: ["notifications-dash"],
    queryFn: () => fetchNotifications(undefined, 20),
    refetchInterval: 30_000,
  });

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl">
      {/* A. Greeting + Headline Hero */}
      <GreetingHero
        headline={canvas?.headline ?? null}
        briefingId={canvas?.briefing_id ?? null}
        approvalCount={canvas?.pending_approvals?.length ?? 0}
        sourceCount={sourceCount}
      />

      {/* E. System Pulse — compact strip */}
      <SystemPulse data={system} />

      {/* B + C + D — Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* B. Priority Inbox */}
        <PriorityInbox
          approvals={canvas?.pending_approvals ?? []}
          notifications={Array.isArray(notifications) ? notifications : []}
        />

        {/* C. Jarvis Activity Timeline */}
        <JarvisActivity
          traces={canvas?.recent_traces ?? []}
          events={canvas?.recent_events ?? []}
        />
      </div>

      {/* D. Goal Momentum */}
      <GoalMomentum goals={canvas?.active_goals ?? []} />
    </div>
  );
}

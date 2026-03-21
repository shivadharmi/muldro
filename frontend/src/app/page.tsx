"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchCanvasDashboard,
  fetchHomeFeed,
} from "@/lib/api";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { SystemPulse } from "@/components/dashboard/system-pulse";
import { GoalMomentum } from "@/components/dashboard/goal-momentum";
import { ChangesSinceAwayStrip } from "@/components/feature/home/changes-since-away";
import { PriorityItemsPanel } from "@/components/feature/home/priority-items-panel";
import { LiveNowPanel } from "@/components/feature/home/live-now-panel";
import { RecommendationPanel } from "@/components/feature/home/recommendation-panel";
import { RecentIntelligenceFeed } from "@/components/feature/home/recent-intelligence-feed";
import { CapabilityHealthRow } from "@/components/feature/home/capability-health-row";

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

  const { data: homeFeed } = useQuery({
    queryKey: ["home-feed"],
    queryFn: fetchHomeFeed,
    refetchInterval: 30_000,
  });

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl">
      {/* Greeting Hero */}
      <GreetingHero
        headline={canvas?.headline ?? null}
        briefingId={canvas?.briefing_id ?? null}
        approvalCount={canvas?.pending_approvals?.length ?? 0}
        sourceCount={sourceCount}
      />

      {/* Changes Since Away */}
      {homeFeed && (
        <ChangesSinceAwayStrip
          sinceLastVisit={homeFeed.since_last_visit ?? "recently"}
          priorityCount={homeFeed.priority_items?.length ?? 0}
          eventCount={homeFeed.live_activity?.length ?? 0}
        />
      )}

      {/* System Pulse */}
      <SystemPulse data={system} />

      {/* Capability Health */}
      <CapabilityHealthRow capabilities={homeFeed?.capability_health ?? []} />

      {/* Grid: Priority + Live + Recommendations */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-1">
          <PriorityItemsPanel items={homeFeed?.priority_items ?? []} />
        </div>
        <div className="lg:col-span-1">
          <LiveNowPanel />
        </div>
        <div className="lg:col-span-1">
          <RecommendationPanel
            actions={homeFeed?.recommended_actions ?? []}
          />
        </div>
      </div>

      {/* Recent Intelligence */}
      <RecentIntelligenceFeed
        items={homeFeed?.recent_intelligence ?? []}
      />

      {/* Goal Momentum */}
      <GoalMomentum goals={canvas?.active_goals ?? []} />
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  fetchSystemDashboard,
  fetchCanvasDashboard,
  fetchHomeFeed,
} from "@/lib/api";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { GoalMomentum } from "@/components/dashboard/goal-momentum";
import { PriorityItemsPanel } from "@/components/feature/home/priority-items-panel";
import { LiveNowPanel } from "@/components/feature/home/live-now-panel";
import { RecommendationPanel } from "@/components/feature/home/recommendation-panel";
import { RecentIntelligenceFeed } from "@/components/feature/home/recent-intelligence-feed";

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
    refetchInterval: 15_000,
  });

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  const priorityCount = homeFeed?.priority_items?.length ?? 0;
  const actionCount = homeFeed?.recommended_actions?.length ?? 0;
  const eventCount = homeFeed?.live_activity?.length ?? 0;
  const hasUrgent = priorityCount > 0 || actionCount > 0;

  const budget = system?.budget;
  const queues = system?.queues;

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Greeting */}
      <GreetingHero
        headline={canvas?.headline ?? null}
        briefingId={canvas?.briefing_id ?? null}
        approvalCount={canvas?.pending_approvals?.length ?? 0}
        sourceCount={sourceCount}
      />

      {/* System status bar */}
      <div className="rounded-xl border border-b-primary bg-surface-0 px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {/* Health indicator */}
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              system?.status === "healthy" ? "bg-green-400" :
              system?.status === "degraded" ? "bg-yellow-400" : "bg-neutral-400"
            }`} />
            <span className="text-xs font-medium text-t-primary capitalize">
              {system?.status ?? "Loading"}
            </span>
          </div>

          <span className="w-px h-4 bg-b-primary hidden sm:block" />

          {/* Budget */}
          {budget && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-t-tertiary">Budget</span>
              <div className="w-20 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${
                    budget.percent_used > 80 ? "bg-red-400" :
                    budget.percent_used > 60 ? "bg-yellow-400" : "bg-accent-primary"
                  }`}
                  style={{ width: `${Math.min(budget.percent_used, 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-t-tertiary font-mono">
                ${budget.daily_spend_usd.toFixed(2)}/{budget.daily_limit_usd.toFixed(0)}
              </span>
            </div>
          )}

          <span className="w-px h-4 bg-b-primary hidden sm:block" />

          {/* Queue */}
          {queues && (
            <div className="flex items-center gap-3 text-[10px] text-t-tertiary">
              <span>{queues.approvals_pending} approvals</span>
              <span>{queues.plans_in_flight} plans</span>
            </div>
          )}

          <span className="w-px h-4 bg-b-primary hidden sm:block" />

          {/* Activity summary pills */}
          <div className="flex items-center gap-2 ml-auto">
            {priorityCount > 0 && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 text-[10px] text-red-400 font-medium">
                {priorityCount} urgent
              </span>
            )}
            {actionCount > 0 && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-orange-500/10 text-[10px] text-orange-400 font-medium">
                {actionCount} action{actionCount > 1 ? "s" : ""}
              </span>
            )}
            {eventCount > 0 && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-500/10 text-[10px] text-blue-400">
                {eventCount} event{eventCount > 1 ? "s" : ""}
              </span>
            )}
            {!hasUrgent && eventCount === 0 && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/10 text-[10px] text-green-400">
                All clear
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Urgent section — only when there are items needing attention */}
      {hasUrgent ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div className="rounded-xl border border-b-primary bg-surface-0 p-4">
            <RecommendationPanel actions={homeFeed?.recommended_actions ?? []} />
          </div>
          <div className="rounded-xl border border-b-primary bg-surface-0 p-4">
            <PriorityItemsPanel items={homeFeed?.priority_items ?? []} />
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-b-primary bg-surface-0 p-6">
          <div className="flex flex-col items-center text-center max-w-sm mx-auto py-2">
            <div className="w-10 h-10 rounded-full bg-green-500/10 flex items-center justify-center mb-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="text-green-400">
                <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2"/>
              </svg>
            </div>
            <p className="text-sm text-t-primary font-medium">Nothing needs your attention</p>
            <p className="text-xs text-t-tertiary mt-1 mb-4">No pending approvals, blocked workflows, or failures.</p>
            <div className="flex gap-2">
              <Link href="/chat" className="px-4 py-2 rounded-lg bg-accent-primary text-white text-xs font-medium hover:opacity-90 transition-opacity">
                Talk to Jarvis
              </Link>
              <Link href="/connectors" className="px-4 py-2 rounded-lg border border-b-primary text-t-secondary text-xs hover:bg-surface-1 transition-colors">
                Connect Sources
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Capabilities + Live Activity + Intelligence — 3 column */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Capabilities */}
        <div className="rounded-xl border border-b-primary bg-surface-0 p-4">
          <CapabilitySection capabilities={homeFeed?.capability_health ?? []} />
        </div>
        {/* Live Activity */}
        <div className="rounded-xl border border-b-primary bg-surface-0 p-4">
          <LiveNowPanel />
        </div>
        {/* Recent Intelligence */}
        <div className="rounded-xl border border-b-primary bg-surface-0 p-4">
          <RecentIntelligenceFeed items={homeFeed?.recent_intelligence ?? []} />
        </div>
      </div>

      {/* Goals */}
      <GoalMomentum goals={canvas?.active_goals ?? []} />
    </div>
  );
}

/* ── Inline capability section with richer display ── */

interface CapHealth {
  family: string;
  status: string;
  provider?: string | null;
  capabilities_available?: number;
  capabilities_total?: number;
  message?: string | null;
}

const CAP_STATUS: Record<string, { dot: string; label: string }> = {
  healthy: { dot: "bg-green-400", label: "Healthy" },
  degraded: { dot: "bg-yellow-400", label: "Degraded" },
  unavailable: { dot: "bg-red-400", label: "Down" },
  unconfigured: { dot: "bg-neutral-500", label: "Not set up" },
  unknown: { dot: "bg-neutral-500", label: "Unknown" },
};

function CapabilitySection({ capabilities }: { capabilities: CapHealth[] }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">Capabilities</h3>
        <Link href="/integrations" className="text-[10px] text-accent-primary hover:underline">Manage</Link>
      </div>
      {capabilities.length === 0 ? (
        <div className="flex items-center gap-3 py-3">
          <div className="w-8 h-8 rounded-full bg-surface-2 flex items-center justify-center shrink-0">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-t-tertiary">
              <path d="M4 4h3v3H4V4zM9 4h3v3H9V4zM4 9h3v3H4V9z" stroke="currentColor" strokeWidth="1.5"/>
            </svg>
          </div>
          <div>
            <p className="text-xs text-t-secondary">No capabilities configured</p>
            <p className="text-[10px] text-t-tertiary">Connect integrations to enable capabilities</p>
          </div>
        </div>
      ) : (
        <div className="space-y-1.5">
          {capabilities.map((cap) => {
            const st = CAP_STATUS[cap.status] || CAP_STATUS.unknown;
            const avail = cap.capabilities_available ?? 0;
            const total = cap.capabilities_total ?? 0;
            return (
              <div key={cap.family} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-surface-1 transition-colors">
                <div className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                  <span className="text-xs text-t-primary capitalize">{cap.family}</span>
                </div>
                <div className="flex items-center gap-2">
                  {total > 0 && (
                    <span className="text-[10px] text-t-tertiary">{avail}/{total}</span>
                  )}
                  {cap.provider && (
                    <span className="text-[10px] text-t-tertiary truncate max-w-[80px]">{cap.provider}</span>
                  )}
                  <span className={`text-[10px] ${cap.status === "healthy" ? "text-green-400" : cap.status === "degraded" ? "text-yellow-400" : "text-t-tertiary"}`}>
                    {st.label}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

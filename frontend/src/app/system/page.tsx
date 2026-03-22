"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchMetrics,
  fetchDLQStats,
  fetchObservationStatus,
  fetchAgentPerformance,
  fetchRuntimeSummary,
  fetchRuntimeRuns,
  fetchRuntimeBlocked,
  fetchAgentWorkload,
  fetchRuntimeActivity,
  triggerHeartbeat,
} from "@/lib/api";
import type { RuntimeSummary, RuntimeRun, RuntimeEvent, AgentWorkload } from "@/lib/types/runtime";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { HealthOverview } from "@/components/system/health-overview";
import { ObservationHealth } from "@/components/system/observation-health";
import { DLQStatsView } from "@/components/system/dlq-stats";
import { AgentUsageTable } from "@/components/dashboard/agent-usage-table";
import { CapabilityHealthGrid } from "@/components/feature/system/capability-health-grid";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "runtime", label: "Runtime" },
  { key: "capabilities", label: "Capabilities" },
  { key: "agents", label: "Agents" },
  { key: "observations", label: "Observations" },
  { key: "dlq", label: "DLQ" },
  { key: "metrics", label: "Metrics" },
];

export default function SystemPage() {
  const [tab, setTab] = useState("overview");

  const { data: dashboard } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: observations } = useQuery({
    queryKey: ["observations"],
    queryFn: fetchObservationStatus,
    enabled: tab === "observations",
  });

  const { data: dlqStats } = useQuery({
    queryKey: ["dlq-stats"],
    queryFn: fetchDLQStats,
    enabled: tab === "dlq",
  });

  const { data: metrics } = useQuery({
    queryKey: ["metrics"],
    queryFn: fetchMetrics,
    enabled: tab === "metrics",
  });

  const { data: agentPerf } = useQuery({
    queryKey: ["agent-perf-system"],
    queryFn: () => fetchAgentPerformance(24),
    enabled: tab === "agents",
  });

  const { data: runtimeSummary } = useQuery({
    queryKey: ["runtime-summary"],
    queryFn: fetchRuntimeSummary,
    enabled: tab === "runtime",
    refetchInterval: 10_000,
  });

  const { data: activeRuns } = useQuery({
    queryKey: ["runtime-runs"],
    queryFn: () => fetchRuntimeRuns(20),
    enabled: tab === "runtime",
    refetchInterval: 10_000,
  });

  const { data: blockedRuns } = useQuery({
    queryKey: ["runtime-blocked"],
    queryFn: fetchRuntimeBlocked,
    enabled: tab === "runtime",
    refetchInterval: 10_000,
  });

  const { data: agentWorkload } = useQuery({
    queryKey: ["runtime-agents"],
    queryFn: fetchAgentWorkload,
    enabled: tab === "runtime",
    refetchInterval: 10_000,
  });

  const { data: recentEvents } = useQuery({
    queryKey: ["runtime-events"],
    queryFn: () => fetchRuntimeActivity(undefined, 30),
    enabled: tab === "runtime",
    refetchInterval: 5_000,
  });

  const heartbeatMut = useMutation({ mutationFn: triggerHeartbeat });

  return (
    <div className="p-4 sm:p-6">
      <PageHeader
        title="System Health"
        subtitle="Monitor system status and diagnostics"
        variant="monitor"
        live
        actions={
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-t-muted">
              Last updated: {new Date().toLocaleTimeString()}
            </span>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => heartbeatMut.mutate()}
              disabled={heartbeatMut.isPending}
            >
              {heartbeatMut.isPending ? "Running..." : "Trigger Heartbeat"}
            </Button>
          </div>
        }
      />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "overview" && dashboard && <HealthOverview data={dashboard} />}

      {tab === "runtime" && (
        <RuntimeCockpit
          summary={runtimeSummary}
          runs={activeRuns}
          blocked={blockedRuns}
          workload={agentWorkload}
          events={recentEvents}
        />
      )}

      {tab === "capabilities" && (
        <div className="mt-4">
          <CapabilityHealthGrid />
        </div>
      )}

      {tab === "agents" && (
        <div className="space-y-4">
          <AgentUsageTable agents={dashboard?.agents} />
          {agentPerf && (
            <div>
              <h3 className="text-sm font-medium mb-3">Cost Breakdown (24h)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {Object.entries(agentPerf.agents)
                  .sort(([, a], [, b]) => b.total_cost_usd - a.total_cost_usd)
                  .map(([name, perf]) => (
                    <Card key={name}>
                      <CardBody>
                        <p className="text-xs text-t-tertiary">{name}</p>
                        <p className="text-sm font-semibold text-t-primary">${perf.total_cost_usd.toFixed(4)}</p>
                        <p className="text-[10px] text-t-muted">
                          {perf.call_count} calls, {perf.error_count} errors
                        </p>
                      </CardBody>
                    </Card>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "observations" && (
        <ObservationHealth observations={observations ?? []} />
      )}

      {tab === "dlq" && <DLQStatsView stats={dlqStats} />}

      {tab === "metrics" && (
        <div>
          {metrics ? (
            <MetricsCards metrics={metrics} />
          ) : (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Card key={i}>
                  <CardBody>
                    <div className="animate-pulse h-6 w-24 bg-surface-2 rounded" />
                  </CardBody>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {heartbeatMut.isSuccess && heartbeatMut.data && (
        <Card className="mt-4">
          <CardHeader>
            <span className="text-sm font-medium text-j-success">Heartbeat Result</span>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-3 gap-3">
              {Object.entries(heartbeatMut.data).map(([key, value]) => (
                <div key={key}>
                  <p className="text-[10px] text-t-muted uppercase">{key.replace(/_/g, " ")}</p>
                  <p className="text-sm text-t-primary">{String(value)}</p>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  running: "bg-blue-500/20 text-blue-400",
  completed: "bg-green-500/20 text-green-400",
  failed: "bg-red-500/20 text-red-400",
  paused: "bg-yellow-500/20 text-yellow-400",
  awaiting_approval: "bg-orange-500/20 text-orange-400",
  cancelled: "bg-neutral-500/20 text-neutral-400",
  pending: "bg-neutral-500/20 text-neutral-400",
};

const EVENT_COLORS: Record<string, string> = {
  run_completed: "text-green-400",
  run_failed: "text-red-400",
  approval_requested: "text-orange-400",
  tool_call_completed: "text-blue-400",
  step_completed: "text-emerald-400",
  command_received: "text-purple-400",
};

function RuntimeCockpit({
  summary,
  runs,
  blocked,
  workload,
  events,
}: {
  summary?: RuntimeSummary;
  runs?: RuntimeRun[];
  blocked?: Array<{ run_id: string; status: string; blocking_steps: Array<{ step_id: string; status: string; action: string | null }> }>;
  workload?: AgentWorkload[];
  events?: RuntimeEvent[];
}) {
  return (
    <div className="mt-4 space-y-6">
      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <Card>
            <CardBody>
              <p className="text-[10px] text-t-muted uppercase">Active Runs</p>
              <p className="text-2xl font-bold text-blue-400">{summary.active_runs}</p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-[10px] text-t-muted uppercase">Blocked</p>
              <p className="text-2xl font-bold text-orange-400">{summary.blocked_runs}</p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-[10px] text-t-muted uppercase">Completed (24h)</p>
              <p className="text-2xl font-bold text-green-400">{summary.completed_24h}</p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-[10px] text-t-muted uppercase">Failed (24h)</p>
              <p className="text-2xl font-bold text-red-400">{summary.failed_24h}</p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-[10px] text-t-muted uppercase">Agents Active</p>
              <p className="text-2xl font-bold text-t-primary">{summary.agents_active}</p>
            </CardBody>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Active runs */}
        <Card>
          <CardHeader><span className="text-sm font-medium">Active Runs</span></CardHeader>
          <CardBody>
            {!runs || runs.length === 0 ? (
              <p className="text-sm text-t-tertiary">No active runs.</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {runs.map((run) => (
                  <div key={run.run_id} className="flex items-center justify-between text-xs p-2 rounded bg-surface-1">
                    <div>
                      <span className="font-mono text-t-secondary">{run.run_id.slice(0, 16)}...</span>
                      {run.plan_id && <span className="ml-2 text-t-tertiary">plan: {run.plan_id.slice(0, 12)}</span>}
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase ${STATUS_COLORS[run.status] || STATUS_COLORS.pending}`}>
                      {run.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Blocked runs */}
        <Card>
          <CardHeader><span className="text-sm font-medium">Blocked Runs</span></CardHeader>
          <CardBody>
            {!blocked || blocked.length === 0 ? (
              <p className="text-sm text-t-tertiary">No blocked runs.</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {blocked.map((run) => (
                  <div key={run.run_id} className="p-2 rounded bg-surface-1 text-xs">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-t-secondary">{run.run_id.slice(0, 16)}...</span>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase ${STATUS_COLORS[run.status] || STATUS_COLORS.pending}`}>
                        {run.status}
                      </span>
                    </div>
                    <div className="text-t-tertiary">
                      {run.blocking_steps.map((s) => (
                        <span key={s.step_id} className="mr-2">{s.action || s.step_id.slice(0, 8)} ({s.status})</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Agent workload */}
        <Card>
          <CardHeader><span className="text-sm font-medium">Agent Workload</span></CardHeader>
          <CardBody>
            {!workload || workload.length === 0 ? (
              <p className="text-sm text-t-tertiary">No agent data.</p>
            ) : (
              <div className="space-y-2">
                {workload.map((w) => (
                  <div key={w.agent_name} className="flex items-center justify-between text-xs">
                    <span className="text-t-primary font-medium">{w.agent_name}</span>
                    <div className="flex items-center gap-3 text-t-tertiary">
                      <span>{w.call_count_24h} calls (24h)</span>
                      <span>{w.avg_duration_ms}ms avg</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Recent events */}
        <Card>
          <CardHeader><span className="text-sm font-medium">Recent Events</span></CardHeader>
          <CardBody>
            {!events || events.length === 0 ? (
              <p className="text-sm text-t-tertiary">No recent events.</p>
            ) : (
              <div className="space-y-1 max-h-64 overflow-y-auto">
                {events.map((evt) => (
                  <div key={evt.event_id} className="flex items-center justify-between text-xs py-1">
                    <div className="flex items-center gap-2">
                      <span className={`font-mono ${EVENT_COLORS[evt.event_type] || "text-t-tertiary"}`}>
                        {evt.event_type}
                      </span>
                      {"tool_name" in evt.payload && (
                        <span className="text-t-tertiary">{String(evt.payload.tool_name)}</span>
                      )}
                    </div>
                    <span className="text-t-tertiary">
                      {new Date(evt.occurred_at).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function MetricsCards({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);

  // Group into stat cards for numeric values, raw for objects
  const numericEntries = entries.filter(([, v]) => typeof v === "number" || typeof v === "string");
  const objectEntries = entries.filter(([, v]) => typeof v === "object" && v !== null);

  return (
    <div className="space-y-4">
      {numericEntries.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {numericEntries.map(([key, value]) => (
            <Card key={key}>
              <CardBody>
                <p className="text-[10px] text-t-muted uppercase">{key.replace(/_/g, " ")}</p>
                <p className="text-lg font-semibold text-t-primary">
                  {typeof value === "number"
                    ? value % 1 !== 0
                      ? value.toFixed(2)
                      : value.toLocaleString()
                    : String(value)}
                </p>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {objectEntries.map(([key, value]) => (
        <Card key={key}>
          <CardHeader>
            <span className="text-sm font-medium">{key.replace(/_/g, " ")}</span>
          </CardHeader>
          <CardBody>
            <pre className="text-xs text-t-secondary font-mono overflow-x-auto">
              {JSON.stringify(value, null, 2)}
            </pre>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

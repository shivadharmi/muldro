"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchMetrics,
  fetchDLQStats,
  fetchObservationStatus,
  fetchAgentPerformance,
  triggerHeartbeat,
} from "@/lib/api";
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

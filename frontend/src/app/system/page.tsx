"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchMetrics,
  fetchDLQStats,
  fetchObservationStatus,
  triggerHeartbeat,
} from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { HealthOverview } from "@/components/system/health-overview";
import { ObservationHealth } from "@/components/system/observation-health";
import { DLQStatsView } from "@/components/system/dlq-stats";
import { AgentUsageTable } from "@/components/dashboard/agent-usage-table";

const TABS = [
  { key: "overview", label: "Overview" },
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
    refetchInterval: tab === "overview" ? 30_000 : false,
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

  const heartbeatMut = useMutation({ mutationFn: triggerHeartbeat });

  return (
    <div className="p-6">
      <PageHeader
        title="System Health"
        subtitle="Monitor system status and diagnostics"
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => heartbeatMut.mutate()}
            disabled={heartbeatMut.isPending}
          >
            {heartbeatMut.isPending ? "Running..." : "Trigger Heartbeat"}
          </Button>
        }
      />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "overview" && dashboard && <HealthOverview data={dashboard} />}

      {tab === "agents" && <AgentUsageTable agents={dashboard?.agents} />}

      {tab === "observations" && (
        <ObservationHealth observations={observations ?? []} />
      )}

      {tab === "dlq" && <DLQStatsView stats={dlqStats} />}

      {tab === "metrics" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Request Metrics</span>
          </CardHeader>
          <CardBody>
            {metrics ? (
              <pre className="text-xs text-neutral-400 font-mono overflow-x-auto">
                {JSON.stringify(metrics, null, 2)}
              </pre>
            ) : (
              <p className="text-xs text-neutral-600">Loading metrics...</p>
            )}
          </CardBody>
        </Card>
      )}

      {heartbeatMut.isSuccess && heartbeatMut.data && (
        <Card className="mt-4">
          <CardHeader>
            <span className="text-sm font-medium text-green-400">Heartbeat Result</span>
          </CardHeader>
          <CardBody>
            <pre className="text-xs text-neutral-400 font-mono">
              {JSON.stringify(heartbeatMut.data, null, 2)}
            </pre>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

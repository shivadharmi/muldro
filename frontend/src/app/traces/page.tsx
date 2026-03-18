"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchTraces,
  fetchAgentPerformance,
  fetchAggregateMetrics,
  fetchTraceDetail,
} from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { TracePanel } from "@/components/runs/trace-panel";
import type { TraceSummary, TraceDetail } from "@/lib/types";

const PAGE_TABS = [
  { key: "traces", label: "Traces" },
  { key: "performance", label: "Performance" },
  { key: "metrics", label: "Metrics" },
];

const TIME_RANGES = [
  { label: "1h", value: 1 },
  { label: "6h", value: 6 },
  { label: "24h", value: 24 },
  { label: "7d", value: 168 },
];

export default function TracesPage() {
  const [tab, setTab] = useState("traces");
  const [timeRange, setTimeRange] = useState(24);
  const [triggerFilter, setTriggerFilter] = useState("");
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const { data: tracesData, isLoading: tracesLoading } = useQuery({
    queryKey: ["traces", timeRange, triggerFilter],
    queryFn: () =>
      fetchTraces(
        timeRange,
        triggerFilter || undefined,
        undefined,
        100
      ),
    enabled: tab === "traces",
    refetchInterval: 60_000,
  });

  const { data: perfData } = useQuery({
    queryKey: ["agent-performance", timeRange],
    queryFn: () => fetchAgentPerformance(timeRange),
    enabled: tab === "performance",
    refetchInterval: 60_000,
  });

  const { data: metricsData } = useQuery({
    queryKey: ["aggregate-metrics", timeRange],
    queryFn: () => fetchAggregateMetrics(timeRange),
    enabled: tab === "metrics",
    refetchInterval: 60_000,
  });

  const { data: traceDetail } = useQuery({
    queryKey: ["trace-detail", selectedTraceId],
    queryFn: () => fetchTraceDetail(selectedTraceId!),
    enabled: !!selectedTraceId,
  });

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Traces"
        subtitle="Observe agent activity, performance, and costs"
      />

      <div className="flex items-center justify-between">
        <Tabs tabs={PAGE_TABS} active={tab} onChange={setTab} />
        <div className="flex gap-1">
          {TIME_RANGES.map((r) => (
            <button
              key={r.value}
              onClick={() => setTimeRange(r.value)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                timeRange === r.value
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-400 hover:text-white"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "traces" && (
        <TracesTab
          traces={tracesData?.traces ?? []}
          count={tracesData?.count ?? 0}
          isLoading={tracesLoading}
          triggerFilter={triggerFilter}
          onTriggerChange={setTriggerFilter}
          onSelect={setSelectedTraceId}
        />
      )}

      {tab === "performance" && perfData && (
        <PerformanceTab agents={perfData.agents} />
      )}

      {tab === "metrics" && metricsData && (
        <MetricsTab metrics={metricsData} />
      )}

      <Modal
        open={!!selectedTraceId}
        onClose={() => setSelectedTraceId(null)}
        title="Trace Detail"
      >
        <TracePanel trace={(traceDetail as TraceDetail) ?? null} />
      </Modal>
    </div>
  );
}

function TracesTab({
  traces,
  count,
  isLoading,
  triggerFilter,
  onTriggerChange,
  onSelect,
}: {
  traces: TraceSummary[];
  count: number;
  isLoading: boolean;
  triggerFilter: string;
  onTriggerChange: (v: string) => void;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <input
          type="text"
          value={triggerFilter}
          onChange={(e) => onTriggerChange(e.target.value)}
          placeholder="Filter by trigger..."
          className="rounded bg-neutral-800 border border-neutral-700 px-3 py-1.5 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <span className="text-xs text-neutral-500 self-center">{count} traces</span>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-900 p-3 animate-pulse">
              <div className="h-4 w-48 bg-neutral-800 rounded mb-1" />
              <div className="h-3 w-32 bg-neutral-800 rounded" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && traces.length === 0 && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">No traces found</p>
          <p className="text-neutral-600 text-xs mt-1">
            Traces appear after Jarvis processes a command.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {traces.map((trace) => (
          <button
            key={trace.trace_id}
            onClick={() => onSelect(trace.trace_id)}
            className="w-full text-left rounded-lg border border-neutral-800 bg-neutral-900 p-3 hover:border-neutral-700 transition-colors"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-neutral-300">
                  {trace.trace_id.slice(0, 12)}...
                </span>
                <Badge variant={statusVariant(trace.status)}>{trace.status}</Badge>
                {trace.trigger && (
                  <span className="text-xs text-neutral-500">{trace.trigger}</span>
                )}
              </div>
              <span className="text-xs text-neutral-500">
                ${trace.total_cost_usd.toFixed(4)}
              </span>
            </div>
            <div className="flex items-center gap-4 mt-1 text-[10px] text-neutral-600">
              <span>{(trace.duration_ms / 1000).toFixed(1)}s</span>
              <span>{trace.agents_invoked.join(", ") || "no agents"}</span>
              {trace.error_count > 0 && (
                <span className="text-red-400">{trace.error_count} error(s)</span>
              )}
              {trace.started_at && (
                <span>{new Date(trace.started_at).toLocaleString()}</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function PerformanceTab({
  agents,
}: {
  agents: Record<string, import("@/lib/types").AgentPerformance>;
}) {
  const entries = Object.entries(agents).sort(
    ([, a], [, b]) => b.total_cost_usd - a.total_cost_usd
  );

  if (entries.length === 0) {
    return (
      <div className="text-center py-12 text-neutral-500 text-sm">
        No agent performance data
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {entries.map(([name, perf]) => (
        <Card key={name}>
          <CardHeader>
            <span className="text-sm font-medium text-white">{name}</span>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <p className="text-neutral-600">Calls</p>
                <p className="text-neutral-300">{perf.call_count}</p>
              </div>
              <div>
                <p className="text-neutral-600">Avg duration</p>
                <p className="text-neutral-300">{(perf.avg_duration_ms / 1000).toFixed(1)}s</p>
              </div>
              <div>
                <p className="text-neutral-600">Total cost</p>
                <p className="text-neutral-300">${perf.total_cost_usd.toFixed(4)}</p>
              </div>
              <div>
                <p className="text-neutral-600">Errors</p>
                <p className={perf.error_count > 0 ? "text-red-400" : "text-neutral-300"}>
                  {perf.error_count}
                </p>
              </div>
              <div>
                <p className="text-neutral-600">Input tokens</p>
                <p className="text-neutral-300">{perf.total_input_tokens.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-neutral-600">Output tokens</p>
                <p className="text-neutral-300">{perf.total_output_tokens.toLocaleString()}</p>
              </div>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

function MetricsTab({ metrics }: { metrics: import("@/lib/types").AggregateMetrics }) {
  const stats = [
    { label: "Total traces", value: metrics.total_traces },
    { label: "Completed", value: metrics.completed },
    { label: "Failed", value: metrics.failed },
    { label: "Success rate", value: `${(metrics.success_rate * 100).toFixed(1)}%` },
    { label: "Avg duration", value: `${(metrics.avg_duration_ms / 1000).toFixed(1)}s` },
    { label: "Total cost", value: `$${metrics.total_cost_usd.toFixed(2)}` },
    { label: "Input tokens", value: metrics.total_input_tokens.toLocaleString() },
    { label: "Output tokens", value: metrics.total_output_tokens.toLocaleString() },
    { label: "Errors", value: metrics.total_errors },
    { label: "Memory writes", value: metrics.total_memory_writes },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
      {stats.map((s) => (
        <Card key={s.label}>
          <CardBody>
            <p className="text-[10px] uppercase text-neutral-600 mb-1">{s.label}</p>
            <p className="text-lg font-semibold text-white">{s.value}</p>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

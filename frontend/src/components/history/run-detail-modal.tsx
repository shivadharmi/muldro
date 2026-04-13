"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHistoryDetail } from "@/lib/api";
import { useHistoryStore } from "@/stores/history-store";

// ── Types ────────────────────────────────────────────────────────────────────

type TabId = "steps" | "plan" | "events" | "trace";

interface ArtifactRef {
  artifact_id?: string;
  name?: string;
  artifact_type?: string;
}

interface DetailStep {
  step_id: string;
  name: string | null;
  capability: string | null;
  status: string;
  output_data: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error: Record<string, unknown> | null;
  artifacts: ArtifactRef[];
}

interface ApprovalRecord {
  approval_id: string;
  step_id: string | null;
  status: string;
  risk_level: string;
  title: string | null;
  decided_at: string | null;
  decision_reason: string | null;
}

interface PlanContext {
  plan_id: string;
  goal: string | null;
  reasoning_summary: string | null;
  success_conditions: unknown[] | null;
  trigger_type: string | null;
  priority: string | null;
}

interface TraceInfo {
  trace_id: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number;
  agents_invoked: string[];
  tools_called: string[];
}

interface EventEntry {
  event_type: string;
  occurred_at: string;
  step_id: string | null;
  payload: Record<string, unknown>;
}

interface RunDetail {
  run_id: string;
  plan: PlanContext | null;
  status: string;
  source: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: Record<string, unknown> | null;
  steps: DetailStep[];
  approvals: ApprovalRecord[];
  trace: TraceInfo | null;
  events: EventEntry[];
}

// ── Constants ────────────────────────────────────────────────────────────────

const TABS: { id: TabId; label: string }[] = [
  { id: "steps", label: "Steps" },
  { id: "plan", label: "Plan" },
  { id: "events", label: "Events" },
  { id: "trace", label: "Trace" },
];

const EVENT_COLORS: Record<string, string> = {
  run_started: "text-blue-400",
  step_started: "text-blue-400",
  step_completed: "text-green-400",
  run_completed: "text-green-400",
  tool_call_started: "text-purple-400",
  approval_requested: "text-yellow-400",
  approval_resolved: "text-green-400",
};

// ── Helper functions ─────────────────────────────────────────────────────────

function getStepIcon(status: string): { icon: string; className: string } {
  switch (status) {
    case "pending":
      return { icon: "\u25CB", className: "text-gray-500 opacity-50" };
    case "ready":
      return { icon: "\u25CB", className: "text-gray-500" };
    case "running":
      return { icon: "\u25C9", className: "text-blue-400 animate-pulse" };
    case "completed":
      return { icon: "\u2713", className: "text-green-400" };
    case "failed":
      return { icon: "\u2717", className: "text-red-400" };
    case "waiting_approval":
      return { icon: "\u25A0", className: "text-yellow-400" };
    case "skipped":
      return { icon: "\u2014", className: "text-gray-500" };
    case "timed_out":
      return { icon: "\u23F1", className: "text-orange-400" };
    default:
      return { icon: "\u25CB", className: "text-gray-500 opacity-50" };
  }
}

function formatDurationMs(ms: number | null): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function getOutputText(step: DetailStep): string | null {
  if (!step.output_data) return null;
  if (typeof step.output_data.text === "string") return step.output_data.text;
  if (typeof step.output_data.result === "string") return step.output_data.result;
  if (typeof step.output_data.summary === "string") return step.output_data.summary;
  return JSON.stringify(step.output_data, null, 2);
}

// ── Tab: Steps ───────────────────────────────────────────────────────────────

function StepsTab({ detail }: { detail: RunDetail }) {
  const approvalByStep: Record<string, ApprovalRecord> = {};
  for (const apr of detail.approvals) {
    if (apr.step_id) approvalByStep[apr.step_id] = apr;
  }

  if (detail.steps.length === 0) {
    return <p className="text-sm text-[#8b949e] text-center py-8">No steps recorded.</p>;
  }

  return (
    <div className="space-y-2">
      {detail.steps.map((step) => {
        const { icon, className: iconClass } = getStepIcon(step.status);
        const duration = formatDurationMs(step.duration_ms);
        const outputText = getOutputText(step);
        const approval = approvalByStep[step.step_id];

        return (
          <div key={step.step_id} className="bg-[#161b22] border border-[#21262d] rounded-lg overflow-hidden">
            {/* Step header */}
            <div className="flex items-center gap-2.5 px-3 py-2.5">
              <span className={`text-sm w-4 shrink-0 text-center leading-none ${iconClass}`}>
                {icon}
              </span>
              <span className="text-sm text-[#e6edf3] truncate flex-1 min-w-0">
                {step.name ?? "Unnamed step"}
              </span>
              {step.capability && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#21262d] text-[#8b949e] shrink-0">
                  {step.capability}
                </span>
              )}
              {duration && (
                <span className="text-[10px] text-[#484f58] shrink-0 tabular-nums">{duration}</span>
              )}
            </div>

            {/* Step body */}
            {(outputText || step.error || step.artifacts.length > 0 || approval) && (
              <div className="border-t border-[#21262d] px-3 pb-3 pt-2 space-y-2">
                {/* Output text */}
                {outputText && (
                  <p className="text-xs text-[#8b949e] whitespace-pre-wrap break-words line-clamp-4">
                    {outputText}
                  </p>
                )}

                {/* Error */}
                {step.error && (
                  <p className="text-xs text-red-400 break-words">
                    {String(step.error.message ?? step.error.detail ?? JSON.stringify(step.error))}
                  </p>
                )}

                {/* Artifacts */}
                {step.artifacts.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {step.artifacts.map((a, i) => (
                      <span
                        key={a.artifact_id ?? i}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-[#21262d] text-[#c9d1d9] border border-[#30363d]"
                      >
                        {a.name ?? a.artifact_type ?? "artifact"}
                      </span>
                    ))}
                  </div>
                )}

                {/* Approval record */}
                {approval && (
                  <div className="flex items-center gap-2 pt-0.5">
                    <span className="text-[10px] text-[#8b949e]">Approval:</span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded font-medium ${
                        approval.status === "approved"
                          ? "bg-green-900/40 text-green-400"
                          : "bg-red-900/40 text-red-400"
                      }`}
                    >
                      {approval.status === "approved" ? "Approved" : "Rejected"}
                    </span>
                    {approval.decision_reason && (
                      <span className="text-[10px] text-[#484f58] truncate">{approval.decision_reason}</span>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Tab: Plan ────────────────────────────────────────────────────────────────

function PlanTab({ plan }: { plan: PlanContext | null }) {
  if (!plan) {
    return <p className="text-sm text-[#8b949e] text-center py-8">No plan context available.</p>;
  }

  const rows: { label: string; value: React.ReactNode }[] = [
    { label: "Goal", value: plan.goal ?? "—" },
    { label: "Reasoning Summary", value: plan.reasoning_summary ?? "—" },
    {
      label: "Success Conditions",
      value:
        plan.success_conditions && plan.success_conditions.length > 0 ? (
          <ul className="list-disc list-inside space-y-0.5">
            {plan.success_conditions.map((c, i) => (
              <li key={i} className="text-[#8b949e]">
                {String(c)}
              </li>
            ))}
          </ul>
        ) : (
          "—"
        ),
    },
    { label: "Priority", value: plan.priority ?? "—" },
    { label: "Trigger Type", value: plan.trigger_type ?? "—" },
  ];

  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.label} className="bg-[#161b22] border border-[#21262d] rounded-lg px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58] mb-1">
            {row.label}
          </p>
          <div className="text-sm text-[#e6edf3]">{row.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Tab: Events ──────────────────────────────────────────────────────────────

function EventsTab({ events }: { events: EventEntry[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-[#8b949e] text-center py-8">No events recorded.</p>;
  }

  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-px bg-[#21262d]" />

      <div className="space-y-3 pl-6">
        {events.map((evt, i) => {
          const colorClass = EVENT_COLORS[evt.event_type] ?? "text-[#8b949e]";
          return (
            <div key={i} className="relative">
              {/* Dot */}
              <div className="absolute -left-6 top-1 w-3.5 h-3.5 rounded-full bg-[#0d1117] border-2 border-[#30363d] flex items-center justify-center">
                <div className={`w-1.5 h-1.5 rounded-full ${colorClass.replace("text-", "bg-")}`} />
              </div>

              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[10px] text-[#484f58] tabular-nums shrink-0">
                  {formatTimestamp(evt.occurred_at)}
                </span>
                <span className={`text-xs font-medium ${colorClass}`}>{evt.event_type}</span>
                {evt.step_id && (
                  <span className="text-[10px] text-[#484f58] font-mono">{evt.step_id.slice(0, 12)}…</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Tab: Trace ───────────────────────────────────────────────────────────────

function TraceTab({ trace }: { trace: TraceInfo | null }) {
  if (!trace) {
    return <p className="text-sm text-[#8b949e] text-center py-8">No trace data available.</p>;
  }

  const metrics = [
    { label: "Input Tokens", value: trace.input_tokens.toLocaleString() },
    { label: "Output Tokens", value: trace.output_tokens.toLocaleString() },
    { label: "Cost ($)", value: `$${trace.cost_usd.toFixed(5)}` },
    { label: "Duration", value: formatDurationMs(trace.duration_ms) || "—" },
  ];

  return (
    <div className="space-y-4">
      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {metrics.map((m) => (
          <div key={m.label} className="bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58] mb-1">
              {m.label}
            </p>
            <p className="text-lg font-semibold text-[#f0f6fc] tabular-nums">{m.value}</p>
          </div>
        ))}
      </div>

      {/* Agents invoked */}
      {trace.agents_invoked.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58] mb-2">
            Agents Invoked
          </p>
          <div className="flex flex-wrap gap-1.5">
            {trace.agents_invoked.map((agent) => (
              <span
                key={agent}
                className="text-xs px-2.5 py-1 rounded-full bg-blue-900/40 text-blue-400"
              >
                {agent}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Tools called */}
      {trace.tools_called.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58] mb-2">
            Tools Called
          </p>
          <div className="flex flex-wrap gap-1.5">
            {trace.tools_called.map((tool, i) => (
              <span
                key={`${tool}-${i}`}
                className="text-xs px-2.5 py-1 rounded-full bg-[#21262d] text-[#c9d1d9]"
              >
                {tool}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Modal ───────────────────────────────────────────────────────────────

export function RunDetailModal() {
  const detailRunId = useHistoryStore((s) => s.detailRunId);
  const detailModalOpen = useHistoryStore((s) => s.detailModalOpen);
  const closeDetail = useHistoryStore((s) => s.closeDetail);

  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("steps");

  const backdropRef = useRef<HTMLDivElement>(null);

  // Reset state when run changes — render-phase pattern (React-recommended
  // alternative to setState-in-effect for "reset when prop changes").
  const [prevRunId, setPrevRunId] = useState<string | null>(null);
  if (prevRunId !== detailRunId) {
    setPrevRunId(detailRunId);
    if (detailRunId) {
      setDetail(null);
      setFetchError(null);
      setActiveTab("steps");
    }
  }

  // Derive loading: we are loading when the modal is open, a run is selected,
  // and we have neither detail data nor an error yet.
  const loading = detailModalOpen && !!detailRunId && !detail && !fetchError;

  // Fetch detail when modal opens — only setState inside async callbacks (allowed).
  useEffect(() => {
    if (!detailModalOpen || !detailRunId) return;
    if (detail) return;
    if (fetchError) return;

    let cancelled = false;

    fetchHistoryDetail(detailRunId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data as unknown as RunDetail);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load run detail";
          setFetchError(msg);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [detailModalOpen, detailRunId, detail, fetchError]);

  // Close on Escape
  useEffect(() => {
    if (!detailModalOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDetail();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [detailModalOpen, closeDetail]);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === backdropRef.current) closeDetail();
    },
    [closeDetail],
  );

  if (!detailModalOpen || !detailRunId) return null;

  const title = detail?.plan?.goal ?? (detail ? `Run ${detail.run_id}` : `Run ${detailRunId}`);
  const subtitle = detail ? `${detail.run_id} · ${detail.status}` : detailRunId;

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4"
    >
      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between px-5 pt-5 pb-3 border-b border-[#21262d]">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-[#f0f6fc] truncate pr-2">{title}</h2>
            <p className="text-[11px] text-[#8b949e] mt-0.5 font-mono truncate">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={closeDetail}
            className="p-1.5 rounded-md hover:bg-[#161b22] transition-colors text-[#8b949e] hover:text-[#e6edf3] cursor-pointer shrink-0 mt-0.5"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M18 6L6 18M6 6l12 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* Tab bar */}
        <div className="flex border-b border-[#21262d] px-5">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2.5 text-[13px] font-medium border-b-2 transition-colors cursor-pointer ${
                activeTab === tab.id
                  ? "border-[#58a6ff] text-[#f0f6fc]"
                  : "border-transparent text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <div className="w-5 h-5 border-2 border-[#58a6ff]/30 border-t-[#58a6ff] rounded-full animate-spin" />
              <span className="ml-2.5 text-sm text-[#8b949e]">Loading...</span>
            </div>
          )}

          {fetchError && !loading && (
            <div className="rounded-lg bg-red-950/20 border border-red-500/20 p-4">
              <p className="text-sm text-red-400">Failed to load: {fetchError}</p>
            </div>
          )}

          {detail && !loading && !fetchError && (
            <>
              {activeTab === "steps" && <StepsTab detail={detail} />}
              {activeTab === "plan" && <PlanTab plan={detail.plan} />}
              {activeTab === "events" && <EventsTab events={detail.events} />}
              {activeTab === "trace" && <TraceTab trace={detail.trace} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

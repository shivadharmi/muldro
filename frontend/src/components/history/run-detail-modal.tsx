"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHistoryDetail } from "@/lib/api";
import { useHistoryStore } from "@/stores/history-store";
import { stepStatusIcon, formatDuration } from "@/components/a2ui/components/step-presentation";

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

// Maps event types to a semantic token text-color so timeline dots/labels match
// the rest of the design system (no hardcoded tailwind palette colors).
const EVENT_COLORS: Record<string, string> = {
  run_started: "text-j-info",
  step_started: "text-j-info",
  step_completed: "text-j-success",
  run_completed: "text-j-success",
  tool_call_started: "text-j-secondary",
  approval_requested: "text-j-warning",
  approval_resolved: "text-j-success",
};

// ── Helper functions ─────────────────────────────────────────────────────────

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

// ── Shared empty state ───────────────────────────────────────────────────────

function EmptyState({ label }: { label: string }) {
  return <p className="text-sm text-t-tertiary text-center py-8">{label}</p>;
}

// ── Tab: Steps ───────────────────────────────────────────────────────────────

function StepsTab({ detail }: { detail: RunDetail }) {
  const approvalByStep: Record<string, ApprovalRecord> = {};
  for (const apr of detail.approvals) {
    if (apr.step_id) approvalByStep[apr.step_id] = apr;
  }

  if (detail.steps.length === 0) {
    return <EmptyState label="No steps recorded." />;
  }

  return (
    <div className="space-y-2">
      {detail.steps.map((step) => {
        const { icon, className: iconClass } = stepStatusIcon(step.status);
        const duration = step.duration_ms != null ? formatDuration(step.duration_ms) : "";
        const outputText = getOutputText(step);
        const approval = approvalByStep[step.step_id];

        return (
          <div
            key={step.step_id}
            className="bg-surface-2 border border-b-secondary rounded-[var(--radius-lg)] overflow-hidden"
          >
            {/* Step header */}
            <div className="flex items-center gap-2.5 px-3 py-2.5">
              <span className={`text-sm w-4 shrink-0 text-center leading-none ${iconClass}`}>
                {icon}
              </span>
              <span className="text-sm text-t-primary truncate flex-1 min-w-0">
                {step.name ?? step.capability ?? step.step_id}
              </span>
              {step.capability && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-3 text-t-tertiary shrink-0">
                  {step.capability}
                </span>
              )}
              {duration && (
                <span className="text-[10px] text-t-muted shrink-0 tabular-nums">{duration}</span>
              )}
            </div>

            {/* Step body */}
            {(outputText || step.error || step.artifacts.length > 0 || approval) && (
              <div className="border-t border-b-secondary px-3 pb-3 pt-2 space-y-2">
                {/* Output text */}
                {outputText && (
                  <p className="text-xs text-t-tertiary whitespace-pre-wrap break-words line-clamp-4">
                    {outputText}
                  </p>
                )}

                {/* Error */}
                {step.error && (
                  <p className="text-xs text-j-error break-words">
                    {String(step.error.message ?? "An error occurred.")}
                  </p>
                )}

                {/* Artifacts */}
                {step.artifacts.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {step.artifacts.map((a, i) => (
                      <span
                        key={a.artifact_id ?? i}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-surface-3 text-t-secondary border border-b-primary"
                      >
                        {a.name ?? a.artifact_type ?? "artifact"}
                      </span>
                    ))}
                  </div>
                )}

                {/* Approval record */}
                {approval && (
                  <div className="flex items-center gap-2 pt-0.5">
                    <span className="text-[10px] text-t-tertiary">Approval:</span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] font-medium ${
                        approval.status === "approved"
                          ? "bg-j-success-soft text-j-success"
                          : "bg-j-error-soft text-j-error"
                      }`}
                    >
                      {approval.status === "approved" ? "Approved" : "Rejected"}
                    </span>
                    {approval.decision_reason && (
                      <span className="text-[10px] text-t-muted truncate">
                        {approval.decision_reason}
                      </span>
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
    return <EmptyState label="No plan context available." />;
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
              <li key={i} className="text-t-tertiary">
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
        <div
          key={row.label}
          className="bg-surface-2 border border-b-secondary rounded-[var(--radius-lg)] px-4 py-3"
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider text-t-muted mb-1">
            {row.label}
          </p>
          <div className="text-sm text-t-primary">{row.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Tab: Events ──────────────────────────────────────────────────────────────

function EventsTab({ events }: { events: EventEntry[] }) {
  if (events.length === 0) {
    return <EmptyState label="No events recorded." />;
  }

  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-px bg-surface-3" />

      <div className="space-y-3 pl-6">
        {events.map((evt, i) => {
          const colorClass = EVENT_COLORS[evt.event_type] ?? "text-t-tertiary";
          return (
            <div key={i} className="relative">
              {/* Dot */}
              <div className="absolute -left-6 top-1 w-3.5 h-3.5 rounded-full bg-surface-0 border-2 border-b-primary flex items-center justify-center">
                <div className={`w-1.5 h-1.5 rounded-full ${colorClass.replace("text-", "bg-")}`} />
              </div>

              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[10px] text-t-muted tabular-nums shrink-0">
                  {formatTimestamp(evt.occurred_at)}
                </span>
                <span className={`text-xs font-medium ${colorClass}`}>{evt.event_type}</span>
                {evt.step_id && (
                  <span className="text-[10px] text-t-muted font-mono">
                    {evt.step_id.slice(0, 12)}…
                  </span>
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
    return <EmptyState label="No trace data available." />;
  }

  const metrics = [
    { label: "Input Tokens", value: trace.input_tokens.toLocaleString() },
    { label: "Output Tokens", value: trace.output_tokens.toLocaleString() },
    { label: "Cost ($)", value: `$${trace.cost_usd.toFixed(5)}` },
    { label: "Duration", value: trace.duration_ms > 0 ? formatDuration(trace.duration_ms) : "—" },
  ];

  return (
    <div className="space-y-4">
      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {metrics.map((m) => (
          <div
            key={m.label}
            className="bg-surface-2 border border-b-secondary rounded-[var(--radius-lg)] px-3 py-3"
          >
            <p className="text-[10px] font-semibold uppercase tracking-wider text-t-muted mb-1">
              {m.label}
            </p>
            <p className="text-lg font-semibold text-t-primary tabular-nums">{m.value}</p>
          </div>
        ))}
      </div>

      {/* Agents invoked */}
      {trace.agents_invoked.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-t-muted mb-2">
            Agents Invoked
          </p>
          <div className="flex flex-wrap gap-1.5">
            {trace.agents_invoked.map((agent) => (
              <span
                key={agent}
                className="text-xs px-2.5 py-1 rounded-full bg-j-info-soft text-j-info"
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
          <p className="text-[10px] font-semibold uppercase tracking-wider text-t-muted mb-2">
            Tools Called
          </p>
          <div className="flex flex-wrap gap-1.5">
            {trace.tools_called.map((tool, i) => (
              <span
                key={`${tool}-${i}`}
                className="text-xs px-2.5 py-1 rounded-full bg-surface-3 text-t-secondary"
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
      <div className="bg-surface-0 border border-b-primary rounded-[var(--radius-xl)] w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col shadow-[var(--shadow-lg)]">
        {/* Header */}
        <div className="flex items-start justify-between px-5 pt-5 pb-3 border-b border-b-secondary">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-t-primary truncate pr-2">{title}</h2>
            <p className="text-[11px] text-t-tertiary mt-0.5 font-mono truncate">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={closeDetail}
            className="p-1.5 rounded-[var(--radius-md)] hover:bg-surface-2 transition-colors text-t-tertiary hover:text-t-primary cursor-pointer shrink-0 mt-0.5"
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
        <div className="flex border-b border-b-secondary px-5">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2.5 text-[13px] font-medium border-b-2 transition-colors cursor-pointer ${
                activeTab === tab.id
                  ? "border-j-primary text-j-primary"
                  : "border-transparent text-t-tertiary hover:text-t-secondary"
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
              <div className="w-5 h-5 border-2 border-j-primary/30 border-t-j-primary rounded-full animate-spin" />
              <span className="ml-2.5 text-sm text-t-tertiary">Loading...</span>
            </div>
          )}

          {fetchError && !loading && (
            <div className="rounded-[var(--radius-lg)] bg-j-error-soft border border-j-error/20 p-4">
              <p className="text-sm text-j-error">Failed to load: {fetchError}</p>
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

"use client";

import { useCallback } from "react";
import type { HistoryItem, HistoryStepSummary, RunApproval } from "@/stores/history-store";
import { useHistoryStore } from "@/stores/history-store";
import type { ApprovalContext } from "@/lib/types/execution";
import { StatusBadge } from "@/components/ui/status-badge";
import { InlineApprovalCard } from "@/components/execution/inline-approval";
import { stepStatusIcon, formatDuration } from "@/components/execution/step-presentation";

/** A rich unified `ApprovalContext` carries evidence fields (e.g. `risk_reasoning`)
 *  the thin `HistoryApprovalContext` fallback never has. */
function isRichApproval(a: RunApproval | null | undefined): a is ApprovalContext {
  return a != null && "risk_reasoning" in a;
}

// ── Props ────────────────────────────────────────────────────────────────────

interface RunRowProps {
  item: HistoryItem;
  onRetry?: (runId: string) => void;
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
}

// ── Helper functions ─────────────────────────────────────────────────────────

function formatRelativeTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Duration in ms between two ISO timestamps, or null when either is missing. */
function durationBetween(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  return new Date(end).getTime() - new Date(start).getTime();
}

// ── Status helpers ───────────────────────────────────────────────────────────

function getStatusLabel(status: string): string {
  if (status === "running" || status === "pending") return "executing";
  if (status === "awaiting_approval") return "approval needed";
  if (status === "timed_out") return "timed out";
  return status.replace("_", " ");
}

// ── Sub-components ───────────────────────────────────────────────────────────

interface StepRowProps {
  step: HistoryStepSummary;
  isCurrentStep: boolean;
}

function StepRow({ step, isCurrentStep }: StepRowProps) {
  const { icon, className: iconClass } = stepStatusIcon(step.status ?? "pending");
  const isPending = step.status === "pending" || step.status === "ready";
  const ms = durationBetween(step.started_at, step.completed_at);
  const duration = ms != null ? formatDuration(ms) : "";

  return (
    <div
      className={`flex items-center gap-2.5 px-3 py-2 rounded-[var(--radius-md)] ${
        isCurrentStep
          ? "border-l-2 border-j-info bg-j-info-soft"
          : isPending
            ? "opacity-40"
            : ""
      }`}
    >
      <span className={`text-sm w-4 shrink-0 text-center leading-none ${iconClass}`}>
        {icon}
      </span>
      <span className="text-xs text-t-primary truncate flex-1">
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
  );
}

interface RunApprovalCardProps {
  approvalId: string;
  stepDescription: string | null;
  riskLevel: string | null;
  trustLevel: string | null;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}

function RunApprovalCard({
  approvalId,
  stepDescription,
  riskLevel,
  trustLevel,
  onApprove,
  onReject,
}: RunApprovalCardProps) {
  const handleApprove = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onApprove(approvalId);
    },
    [approvalId, onApprove],
  );

  const handleReject = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onReject(approvalId);
    },
    [approvalId, onReject],
  );

  return (
    <div className="rounded-[var(--radius-lg)] border border-j-warning/20 bg-j-warning-soft p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="text-j-warning text-sm">&#9888;</span>
        <span className="text-xs font-medium text-t-primary">Approval Required</span>
      </div>

      {stepDescription && <p className="text-xs text-t-tertiary">{stepDescription}</p>}

      <div className="flex flex-wrap gap-1.5">
        {riskLevel && (
          <span className="text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] bg-j-warning-soft text-j-warning font-semibold uppercase tracking-wider">
            {riskLevel} risk
          </span>
        )}
        {trustLevel && (
          <span className="text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] bg-surface-3 text-t-tertiary">
            {trustLevel.replace("_", " ")}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={handleApprove}
          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-surface-0 hover:opacity-90 transition-opacity cursor-pointer"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-j-error border border-j-error/20 hover:bg-j-error-soft transition-colors cursor-pointer"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

const ACTIVE_STATUSES = new Set(["running", "pending", "awaiting_approval"]);

export function RunRow({ item, onRetry, onApprove, onReject }: RunRowProps) {
  const openDetail = useHistoryStore((s) => s.openDetail);

  const isActive = ACTIVE_STATUSES.has(item.status);
  const isExpanded = isActive;
  const isFailed = item.status === "failed";

  const handleRowClick = useCallback(() => {
    openDetail(item.run_id);
  }, [item.run_id, openDetail]);

  const handleRetry = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onRetry?.(item.run_id);
    },
    [item.run_id, onRetry],
  );

  // Find the current running step for highlighting
  const currentStepId =
    item.steps.find((s) => s.status === "running")?.step_id ?? null;

  // Build subtitle: trigger · time · steps. Cost/agent/duration get their own
  // dedicated, typed cells on the right so they read consistently.
  const subtitleParts: string[] = [];
  if (item.trigger_type) subtitleParts.push(item.trigger_type);
  else if (item.source) subtitleParts.push(item.source);
  if (item.started_at) subtitleParts.push(formatRelativeTime(item.started_at));
  if (item.step_count > 0) {
    subtitleParts.push(`${item.completed_step_count}/${item.step_count} steps`);
  }

  // Prefer the explicit run duration; fall back to start/complete delta.
  const durationMs =
    item.duration_ms ?? durationBetween(item.started_at, item.completed_at);
  const durationText = durationMs != null ? formatDuration(durationMs) : null;

  const goal = item.goal ?? `Run ${item.run_id.slice(0, 16)}`;
  const statusLabel = getStatusLabel(item.status);

  return (
    <div
      className="border-b border-b-secondary hover:bg-surface-1/50 transition-colors cursor-pointer"
      onClick={handleRowClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openDetail(item.run_id);
        }
      }}
      aria-label={`Run: ${goal}`}
    >
      {/* Header row */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Goal text */}
        <span className="text-sm text-t-primary truncate flex-1 min-w-0">{goal}</span>

        {/* Subtitle (hidden when expanded active, shown when collapsed) */}
        {!isExpanded && subtitleParts.length > 0 && (
          <span className="text-xs text-t-tertiary shrink-0 hidden sm:block truncate max-w-[260px]">
            {subtitleParts.join(" · ")}
          </span>
        )}

        {/* Agent attribution */}
        {item.agent && (
          <span className="text-[10px] text-t-muted font-mono shrink-0 hidden md:block">
            {item.agent.toLowerCase()}
          </span>
        )}

        {/* Duration */}
        {durationText && (
          <span className="text-[11px] text-t-tertiary shrink-0 tabular-nums hidden sm:block">
            {durationText}
          </span>
        )}

        {/* Cost */}
        <span className="text-[11px] text-j-success shrink-0 tabular-nums w-[60px] text-right">
          {item.cost_usd != null ? `$${item.cost_usd.toFixed(3)}` : "—"}
        </span>

        {/* Status badge */}
        <span className="shrink-0">
          <StatusBadge status={item.status} label={statusLabel} />
        </span>

        {/* Retry button for failed runs */}
        {isFailed && onRetry && (
          <button
            type="button"
            onClick={handleRetry}
            className="text-[11px] px-2.5 py-1 rounded-[var(--radius-md)] bg-surface-2 text-t-tertiary hover:text-t-primary hover:bg-surface-3 border border-b-primary transition-colors cursor-pointer shrink-0"
          >
            Retry
          </button>
        )}
      </div>

      {/* Expanded section: steps + approval */}
      {isExpanded && (
        <div
          className="mx-4 mb-3 rounded-[var(--radius-lg)] bg-surface-1 overflow-hidden"
          onClick={(e) => e.stopPropagation()}
          role="presentation"
        >
          {/* Subtitle shown below header when expanded */}
          {subtitleParts.length > 0 && (
            <div className="px-3 pt-2 pb-1">
              <span className="text-[11px] text-t-tertiary">
                {subtitleParts.join(" · ")}
              </span>
            </div>
          )}

          {/* Step list */}
          {item.steps.length > 0 && (
            <div className="py-1.5">
              {item.steps.map((step) => (
                <StepRow
                  key={step.step_id ?? step.name}
                  step={step}
                  isCurrentStep={
                    step.step_id != null && step.step_id === currentStepId
                  }
                />
              ))}
            </div>
          )}

          {/* Approval card — the unified InlineApprovalCard when the REST/WS payload
              carries a rich ApprovalContext (self-wires approve/edit/reject via the
              WS action store); the legacy thin RunApprovalCard is the fallback. */}
          {item.status === "awaiting_approval" && isRichApproval(item.approval) && (
            <div className="px-3 pb-3">
              <InlineApprovalCard approval={item.approval} />
            </div>
          )}
          {item.status === "awaiting_approval" &&
            !isRichApproval(item.approval) &&
            item.approval != null &&
            item.approval.approval_id != null &&
            onApprove != null &&
            onReject != null && (
              <div className="px-3 pb-3">
                <RunApprovalCard
                  approvalId={item.approval.approval_id}
                  stepDescription={item.approval.step_description}
                  riskLevel={item.approval.risk_level}
                  trustLevel={item.approval.trust_level}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              </div>
            )}
        </div>
      )}
    </div>
  );
}

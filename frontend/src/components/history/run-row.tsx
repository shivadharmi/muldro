"use client";

import { useCallback } from "react";
import type { HistoryItem, HistoryStepSummary } from "@/stores/history-store";
import { useHistoryStore } from "@/stores/history-store";

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

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── Status helpers ───────────────────────────────────────────────────────────

function getStatusLabel(status: string): string {
  if (status === "running" || status === "pending") return "executing";
  if (status === "awaiting_approval") return "approval needed";
  return status;
}

function getRunDotClass(status: string): string {
  switch (status) {
    case "running":
    case "pending":
      return "bg-blue-400 animate-pulse shadow-[0_0_6px_rgba(96,165,250,0.6)]";
    case "completed":
      return "bg-green-400";
    case "failed":
      return "bg-red-400";
    case "awaiting_approval":
      return "bg-yellow-400";
    case "cancelled":
    default:
      return "bg-gray-500";
  }
}

function getRunBadgeClass(status: string): string {
  switch (status) {
    case "running":
    case "pending":
      return "bg-blue-900/40 text-blue-400";
    case "completed":
      return "bg-green-900/40 text-green-400";
    case "failed":
      return "bg-red-900/40 text-red-400";
    case "awaiting_approval":
      return "bg-yellow-900/40 text-yellow-400";
    case "cancelled":
    default:
      return "bg-gray-800 text-gray-400";
  }
}

function getStepIcon(status: string | null): { icon: string; className: string } {
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
    case "cancelled":
      return { icon: "\u2298", className: "text-gray-500" };
    default:
      return { icon: "\u25CB", className: "text-gray-500 opacity-50" };
  }
}

// ── Sub-components ───────────────────────────────────────────────────────────

interface StepRowProps {
  step: HistoryStepSummary;
  isCurrentStep: boolean;
}

function StepRow({ step, isCurrentStep }: StepRowProps) {
  const { icon, className: iconClass } = getStepIcon(step.status);
  const isPending = step.status === "pending" || step.status === "ready";
  const duration = formatDuration(step.started_at, step.completed_at);

  return (
    <div
      className={`flex items-center gap-2.5 px-3 py-2 rounded ${
        isCurrentStep
          ? "border-l-2 border-blue-400 bg-blue-900/10"
          : isPending
            ? "opacity-40"
            : ""
      }`}
    >
      <span className={`text-sm w-4 shrink-0 text-center leading-none ${iconClass}`}>
        {icon}
      </span>
      <span className="text-xs text-[#e6edf3] truncate flex-1">
        {step.name ?? "Unnamed step"}
      </span>
      {step.capability && (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#21262d] text-[#8b949e] shrink-0">
          {step.capability}
        </span>
      )}
      {duration && (
        <span className="text-[10px] text-[#8b949e] shrink-0 tabular-nums">{duration}</span>
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

  const riskClass =
    riskLevel === "high"
      ? "border-red-500/30 bg-red-950/20"
      : "border-yellow-500/30 bg-yellow-950/20";

  return (
    <div className={`rounded-lg border ${riskClass} bg-[#1c1e24] border-[#30363d] p-3 space-y-2.5`}>
      <div className="flex items-center gap-2">
        <span className="text-yellow-400 text-sm">&#9888;</span>
        <span className="text-xs font-medium text-[#e6edf3]">Approval Required</span>
      </div>

      {stepDescription && (
        <p className="text-xs text-[#8b949e]">{stepDescription}</p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {riskLevel && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-yellow-900/40 text-yellow-400 font-semibold uppercase tracking-wider">
            {riskLevel} risk
          </span>
        )}
        {trustLevel && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-[#21262d] text-[#8b949e]">
            {trustLevel.replace("_", " ")}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={handleApprove}
          className="px-3 py-1.5 text-xs font-medium rounded bg-green-700 text-white hover:bg-green-600 transition-colors cursor-pointer"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-3 py-1.5 text-xs font-medium rounded bg-[#1c1e24] text-red-400 border border-red-500/20 hover:bg-red-950/30 transition-colors cursor-pointer"
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

  // Build subtitle: trigger · time · steps · duration · cost
  const subtitleParts: string[] = [];
  if (item.source) subtitleParts.push(item.source);
  if (item.started_at) subtitleParts.push(formatRelativeTime(item.started_at));
  if (item.total_steps != null) {
    const completed = item.completed_steps ?? 0;
    subtitleParts.push(`${completed}/${item.total_steps} steps`);
  }
  const dur = formatDuration(item.started_at, item.completed_at);
  if (dur) subtitleParts.push(dur);
  if (item.total_cost_usd != null && item.total_cost_usd > 0) {
    subtitleParts.push(`$${item.total_cost_usd.toFixed(4)}`);
  }

  const goal = item.intent ?? item.capability_summary ?? "Untitled run";
  const statusLabel = getStatusLabel(item.status);

  return (
    <div
      className="border-b border-[#21262d] hover:bg-[#161b22]/50 transition-colors cursor-pointer"
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
        {/* Status dot */}
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${getRunDotClass(item.status)}`}
          aria-hidden="true"
        />

        {/* Goal text */}
        <span className="text-sm text-[#e6edf3] truncate flex-1 min-w-0">{goal}</span>

        {/* Subtitle (hidden when expanded active, shown when collapsed) */}
        {!isExpanded && subtitleParts.length > 0 && (
          <span className="text-xs text-[#8b949e] shrink-0 hidden sm:block truncate max-w-[300px]">
            {subtitleParts.join(" · ")}
          </span>
        )}

        {/* Status badge */}
        <span
          className={`text-[11px] px-2 py-0.5 rounded shrink-0 capitalize ${getRunBadgeClass(item.status)}`}
        >
          {statusLabel}
        </span>

        {/* Retry button for failed runs */}
        {isFailed && onRetry && (
          <button
            type="button"
            onClick={handleRetry}
            className="text-[11px] px-2.5 py-1 rounded bg-[#21262d] text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#30363d] border border-[#30363d] transition-colors cursor-pointer shrink-0"
          >
            Retry
          </button>
        )}
      </div>

      {/* Expanded section: steps + approval */}
      {isExpanded && (
        <div
          className="mx-4 mb-3 rounded-lg bg-[#161b22] overflow-hidden"
          onClick={(e) => e.stopPropagation()}
          role="presentation"
        >
          {/* Subtitle shown below header when expanded */}
          {subtitleParts.length > 0 && (
            <div className="px-3 pt-2 pb-1">
              <span className="text-[11px] text-[#8b949e]">
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

          {/* Approval card */}
          {item.status === "awaiting_approval" &&
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

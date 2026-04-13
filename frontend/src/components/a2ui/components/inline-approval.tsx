"use client";

import { useState, useCallback, useEffect } from "react";
import type { ApprovalContext } from "@/lib/a2ui-types";
import { useWsActionStore } from "@/stores/ws-action-store";
import { riskLevelColor, riskLevelTextColor, trustLevelColor } from "@/lib/design-tokens";
import { Modal } from "@/components/ui/modal";

function useCountdown(expiresAt: string | null): number {
  const [remainingMs, setRemainingMs] = useState(() => {
    if (!expiresAt) return Infinity;
    return new Date(expiresAt).getTime() - Date.now();
  });

  useEffect(() => {
    if (!expiresAt) return;
    const tick = () => setRemainingMs(new Date(expiresAt).getTime() - Date.now());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  return remainingMs;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "Expired";
  const totalSec = Math.ceil(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min > 0) return `${min}m ${sec.toString().padStart(2, "0")}s`;
  return `${sec}s`;
}

interface InlineApprovalCardProps {
  approval: ApprovalContext;
}

export function InlineApprovalCard({ approval }: InlineApprovalCardProps) {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const [showRejectConfirm, setShowRejectConfirm] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const remainingMs = useCountdown(approval.expires_at ?? null);
  const isExpired = approval.expires_at != null && remainingMs <= 0;
  const isUrgent = approval.expires_at != null && remainingMs > 0 && remainingMs <= 120_000;

  const handleApprove = useCallback(() => {
    if (isExpired) return;
    sendAction("approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id, isExpired]);

  const handleRejectClick = useCallback(() => {
    if (isExpired) return;
    setShowRejectConfirm(true);
  }, [isExpired]);

  const handleRejectConfirm = useCallback(() => {
    sendAction("reject", { id: approval.approval_id, reason: rejectReason || undefined });
    setShowRejectConfirm(false);
    setRejectReason("");
  }, [sendAction, approval.approval_id, rejectReason]);

  const handleEdit = useCallback(() => {
    if (isExpired) return;
    sendAction("edit_before_approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id, isExpired]);

  const riskBorder = approval.risk_level === "high" ? "border-j-error/30" : "border-j-warning/30";
  const riskBg = approval.risk_level === "high" ? "bg-j-error-soft" : "bg-j-warning-soft";

  return (
    <>
      <div className={`rounded-[var(--radius-lg)] border ${riskBorder} ${riskBg} p-4 space-y-3`}>
        {/* Header with countdown */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-j-warning">&#9888;</span>
            <span className="text-sm font-medium text-t-primary">Approval Required</span>
          </div>
          {approval.expires_at && (
            <span
              className={`text-[11px] flex items-center gap-1 ${
                isExpired
                  ? "text-j-error font-medium"
                  : isUrgent
                    ? "text-j-error animate-pulse"
                    : "text-j-warning"
              }`}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="none"
                className="opacity-70"
              >
                <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.2" />
                <path
                  d="M8 4.5V8.5L10.5 10"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinecap="round"
                />
              </svg>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>
                {formatCountdown(remainingMs)}
              </span>
            </span>
          )}
        </div>

        {/* Step description */}
        <p className="text-sm font-semibold text-t-primary">{approval.step_description}</p>

        {/* Primary badges: risk + trust + reversibility */}
        <div className="flex flex-wrap gap-1.5">
          {approval.risk_level && (
            <span
              className={`text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] ${riskLevelColor(approval.risk_level)} ${riskLevelTextColor(approval.risk_level)} font-semibold uppercase tracking-wider`}
            >
              {approval.risk_level} risk
            </span>
          )}
          {approval.trust_level && (
            <span
              className={`text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] ${trustLevelColor(approval.trust_level)} text-white font-medium`}
            >
              {approval.trust_level.replace("_", " ")}
            </span>
          )}
          {!approval.reversible && (
            <span className="text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-tertiary">
              Irreversible
            </span>
          )}
        </div>

        {/* Evidence section (collapsible) */}
        <details className="group">
          <summary className="text-[11px] text-t-muted cursor-pointer select-none py-1 hover:text-t-secondary transition-colors">
            Why does this need approval?
          </summary>
          <div className="rounded-[var(--radius-md)] bg-surface-1 border-l-[3px] border-l-j-warning p-3 space-y-1.5 mt-1.5 animate-fade-in">
            <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
            <div className="flex flex-wrap gap-2 text-[10px] text-t-muted">
              {approval.blast_radius !== "self" && (
                <span>Blast radius: {approval.blast_radius.replace("_", " ")}</span>
              )}
              {approval.approved_count > 0 && (
                <span>Approved: {approval.approved_count}</span>
              )}
              {approval.rejected_count > 0 && (
                <span>Rejected: {approval.rejected_count}</span>
              )}
            </div>
          </div>
        </details>

        {/* Graduation hint */}
        {approval.graduation_hint && (
          <div className="bg-j-info-soft rounded-[var(--radius-md)] px-3 py-2 flex items-start gap-2">
            <svg
              width="14"
              height="14"
              viewBox="0 0 16 16"
              fill="none"
              className="text-j-info shrink-0 mt-0.5"
            >
              <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.3" />
              <path
                d="M8 7v4M8 5.5v0"
                stroke="currentColor"
                strokeWidth="1.3"
                strokeLinecap="round"
              />
            </svg>
            <p className="text-xs text-j-info">{approval.graduation_hint}</p>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-2.5 pt-1">
          <button
            type="button"
            onClick={handleApprove}
            disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={handleEdit}
            disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={handleRejectClick}
            disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Reject
          </button>
        </div>
      </div>

      {/* Reject confirmation modal */}
      <Modal
        open={showRejectConfirm}
        onClose={() => setShowRejectConfirm(false)}
        title="Reject this action?"
        size="sm"
      >
        <div className="space-y-3">
          <p className="text-sm text-t-secondary">
            This will cancel &ldquo;{approval.step_description}&rdquo;. The task will be marked
            as rejected.
          </p>
          <div>
            <label className="text-xs text-t-muted block mb-1">Optionally explain why:</label>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="e.g., wrong recipients, needs review first"
              rows={2}
              className="w-full text-xs bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] px-3 py-2 text-t-primary placeholder:text-t-muted resize-none focus:outline-none focus:ring-1 focus:ring-j-primary/50"
            />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setShowRejectConfirm(false)}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleRejectConfirm}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer"
            >
              Yes, Reject
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}

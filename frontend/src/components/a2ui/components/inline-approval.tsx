"use client";

import { useCallback } from "react";
import type { ApprovalContext } from "@/lib/a2ui-types";
import { useWsActionStore } from "@/stores/ws-action-store";

interface InlineApprovalCardProps {
  approval: ApprovalContext;
}

export function InlineApprovalCard({ approval }: InlineApprovalCardProps) {
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleApprove = useCallback(() => {
    sendAction("approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  const handleReject = useCallback(() => {
    sendAction("reject", { id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  const handleEdit = useCallback(() => {
    sendAction("edit_before_approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  return (
    <div className="rounded-[var(--radius-lg)] border border-j-warning/30 bg-j-warning-soft p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-j-warning">&#9888;</span>
        <span className="text-sm font-medium text-t-primary">Approval Required</span>
      </div>

      {/* Step description — promoted to primary */}
      <p className="text-sm font-semibold text-t-primary">{approval.step_description}</p>

      {/* Risk reasoning — with warning accent border */}
      <div className="rounded-[var(--radius-md)] bg-surface-1 border-l-[3px] border-l-j-warning p-3 space-y-1.5">
        <div className="flex items-center gap-2">
          <p className="text-xs font-medium text-t-secondary">Risk Assessment</p>
          <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-j-warning-soft text-j-warning font-medium uppercase">
            review
          </span>
        </div>
        <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
      </div>

      {/* Trust context — structured display */}
      <div className="text-xs">
        <span className="font-medium text-t-secondary">Trust: </span>
        <span className="text-t-tertiary">{approval.trust_context}</span>
      </div>

      {/* Graduation hint — callout box, moved above buttons */}
      {approval.graduation_hint && (
        <div className="bg-j-info-soft rounded-[var(--radius-md)] px-3 py-2 flex items-start gap-2">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="text-j-info shrink-0 mt-0.5">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.3" />
            <path d="M8 7v4M8 5.5v0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          <p className="text-xs text-j-info">{approval.graduation_hint}</p>
        </div>
      )}

      {/* Action buttons — equal weight */}
      <div className="flex items-center gap-2.5 pt-1">
        <button
          type="button"
          onClick={handleApprove}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors cursor-pointer"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleEdit}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

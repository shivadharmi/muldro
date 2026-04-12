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
        <span className="text-j-warning">⚠</span>
        <span className="text-sm font-medium text-t-primary">Approval Required</span>
      </div>

      {/* Step description */}
      <p className="text-sm text-t-secondary">{approval.step_description}</p>

      {/* Risk reasoning */}
      <div className="rounded bg-surface-1 p-3 space-y-2">
        <p className="text-xs font-medium text-t-secondary">Risk Assessment</p>
        <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
      </div>

      {/* Trust context */}
      <div className="text-xs text-t-tertiary">
        <span className="font-medium text-t-secondary">Trust: </span>
        {approval.trust_context}
      </div>

      {/* Graduation hint */}
      {approval.graduation_hint && (
        <p className="text-xs text-j-info/80 italic">
          {approval.graduation_hint}
        </p>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={handleApprove}
          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleEdit}
          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-b-primary text-t-secondary hover:bg-surface-1 transition-colors"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] text-j-error hover:bg-j-error-soft transition-colors"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

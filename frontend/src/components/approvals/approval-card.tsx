"use client";

import { useState } from "react";
import type { Approval } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, riskVariant, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeAgo } from "@/components/ui/time-ago";

export function ApprovalCard({
  approval,
  onApprove,
  onReject,
}: {
  approval: Approval & { plan_goal?: string; trace_id?: string };
  onApprove: (id: string, reason?: string) => void;
  onReject: (id: string, reason?: string) => void;
}) {
  const [reason, setReason] = useState("");
  const isPending = approval.status === "pending";

  const isHighRisk = approval.risk_level === "high" || approval.risk_level === "critical";

  return (
    <Card className={isHighRisk ? "glow-error border-j-error/30" : ""}>
      <CardBody>
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1">
            <p className="text-sm font-medium">{approval.title}</p>
            {approval.plan_goal && (
              <p className="text-xs text-j-primary mt-1 font-medium">
                Goal: {String(approval.plan_goal)}
              </p>
            )}
            {approval.summary && (
              <p className="text-xs text-t-secondary mt-1">{approval.summary}</p>
            )}
          </div>
          <div className="flex items-center gap-2 ml-3">
            <Badge variant={riskVariant(approval.risk_level)}>{approval.risk_level}</Badge>
            <Badge variant={statusVariant(approval.status)}>{approval.status}</Badge>
          </div>
        </div>

        <div className="flex items-center justify-between mt-3">
          <div className="flex items-center gap-3">
            <TimeAgo date={approval.created_at} className="text-xs" />
            {approval.trace_id && (
              <a
                href={`/traces?id=${String(approval.trace_id)}`}
                className="text-xs text-j-primary hover:text-j-primary"
                onClick={(e) => e.stopPropagation()}
              >
                View reasoning
              </a>
            )}
          </div>

          {isPending && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Reason (optional)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="bg-surface-2 border border-b-primary rounded px-2 py-1 text-xs text-t-primary w-40 placeholder:text-t-muted"
              />
              <Button
                size="sm"
                variant="primary"
                onClick={() => onApprove(approval.approval_id, reason || undefined)}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={() => onReject(approval.approval_id, reason || undefined)}
              >
                Reject
              </Button>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

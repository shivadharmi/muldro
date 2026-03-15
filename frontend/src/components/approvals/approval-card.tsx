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
  approval: Approval;
  onApprove: (id: string, reason?: string) => void;
  onReject: (id: string, reason?: string) => void;
}) {
  const [reason, setReason] = useState("");
  const isPending = approval.status === "pending";

  return (
    <Card>
      <CardBody>
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1">
            <p className="text-sm font-medium">{approval.title}</p>
            {approval.summary && (
              <p className="text-xs text-neutral-400 mt-1">{approval.summary}</p>
            )}
          </div>
          <div className="flex items-center gap-2 ml-3">
            <Badge variant={riskVariant(approval.risk_level)}>{approval.risk_level}</Badge>
            <Badge variant={statusVariant(approval.status)}>{approval.status}</Badge>
          </div>
        </div>

        <div className="flex items-center justify-between mt-3">
          <TimeAgo date={approval.created_at} className="text-xs" />

          {isPending && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Reason (optional)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs text-neutral-300 w-40 placeholder:text-neutral-600"
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

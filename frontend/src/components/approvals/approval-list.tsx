"use client";

import { useState } from "react";
import type { Approval } from "@/lib/types";
import { Tabs } from "@/components/ui/tabs";
import { EmptyState } from "@/components/ui/empty-state";
import { ApprovalCard } from "./approval-card";

const FILTER_TABS = [
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "all", label: "All" },
];

export function ApprovalList({
  approvals,
  onApprove,
  onReject,
}: {
  approvals: Approval[];
  onApprove: (id: string, reason?: string) => void;
  onReject: (id: string, reason?: string) => void;
}) {
  const [filter, setFilter] = useState("pending");

  const filtered =
    filter === "all"
      ? approvals
      : approvals.filter((a) => a.status === filter);

  return (
    <div>
      <Tabs tabs={FILTER_TABS} active={filter} onChange={setFilter} />

      {filtered.length === 0 ? (
        <EmptyState title={`No ${filter} approvals`} />
      ) : (
        <div className="space-y-3">
          {filtered.map((a) => (
            <ApprovalCard
              key={a.approval_id}
              approval={a}
              onApprove={onApprove}
              onReject={onReject}
            />
          ))}
        </div>
      )}
    </div>
  );
}

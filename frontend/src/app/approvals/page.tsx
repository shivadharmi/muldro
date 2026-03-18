"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchApprovals, fetchApproval, approveAction, rejectAction } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { EmptyState } from "@/components/ui/empty-state";
import { ApprovalCard } from "@/components/approvals/approval-card";
import { Modal } from "@/components/ui/modal";
import { Badge, riskVariant, statusVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import type { ApprovalDetail } from "@/lib/types";
import Link from "next/link";

const FILTER_TABS = [
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "all", label: "All" },
];

export default function ApprovalsPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("pending");
  const [detailId, setDetailId] = useState<string | null>(null);

  const statusParam = filter === "all" ? undefined : filter;
  const { data: approvals = [], isLoading } = useQuery({
    queryKey: ["approvals", filter],
    queryFn: () => fetchApprovals(statusParam),
    refetchInterval: 15_000,
  });

  const { data: detail } = useQuery({
    queryKey: ["approval-detail", detailId],
    queryFn: () => fetchApproval(detailId!),
    enabled: !!detailId,
  });

  const approveMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      approveAction(id, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });

  const rejectMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      rejectAction(id, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });

  return (
    <div className="p-6 space-y-4">
      <PageHeader title="Approvals" subtitle="Review and approve pending actions" />

      <Tabs tabs={FILTER_TABS} active={filter} onChange={setFilter} />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-900 p-4 animate-pulse">
              <div className="h-4 w-48 bg-neutral-800 rounded mb-2" />
              <div className="h-3 w-32 bg-neutral-800 rounded" />
            </div>
          ))}
        </div>
      ) : approvals.length === 0 ? (
        <EmptyState
          title={filter === "pending" ? "All clear!" : `No ${filter} approvals`}
          description={filter === "pending" ? "No pending approvals right now." : undefined}
        />
      ) : (
        <div className="space-y-3">
          {approvals.map((a) => (
            <div key={a.approval_id} onClick={() => setDetailId(a.approval_id)} className="cursor-pointer">
              <ApprovalCard
                approval={a}
                onApprove={(id, reason) => approveMut.mutate({ id, reason })}
                onReject={(id, reason) => rejectMut.mutate({ id, reason })}
              />
            </div>
          ))}
        </div>
      )}

      <Modal
        open={!!detailId}
        onClose={() => setDetailId(null)}
        title="Approval Detail"
      >
        {detail && <ApprovalDetailView detail={detail} />}
      </Modal>
    </div>
  );
}

function ApprovalDetailView({ detail }: { detail: ApprovalDetail }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant={statusVariant(detail.status)}>{detail.status}</Badge>
        <Badge variant={riskVariant(detail.risk_level)}>{detail.risk_level} risk</Badge>
        {detail.approval_type && (
          <Badge variant="default">{detail.approval_type}</Badge>
        )}
      </div>

      <div>
        <h4 className="text-sm font-medium text-white">{detail.title}</h4>
        {detail.summary && (
          <p className="text-xs text-neutral-400 mt-1">{detail.summary}</p>
        )}
      </div>

      {detail.plan_goal && (
        <div>
          <p className="text-[10px] uppercase text-neutral-600 mb-0.5">Plan Goal</p>
          <p className="text-xs text-neutral-300">{detail.plan_goal}</p>
        </div>
      )}

      {detail.execution_id && (
        <div>
          <p className="text-[10px] uppercase text-neutral-600 mb-0.5">Execution</p>
          <Link href={`/runs/${detail.execution_id}`} className="text-xs text-blue-400 hover:text-blue-300">
            {detail.execution_id}
          </Link>
        </div>
      )}

      {detail.decision_reason && (
        <div>
          <p className="text-[10px] uppercase text-neutral-600 mb-0.5">Decision Reason</p>
          <p className="text-xs text-neutral-300">{detail.decision_reason}</p>
        </div>
      )}

      <div className="flex gap-4 text-[10px] text-neutral-600">
        {detail.created_at && <span>Created <TimeAgo date={detail.created_at} /></span>}
        {detail.decided_at && <span>Decided <TimeAgo date={detail.decided_at} /></span>}
      </div>
    </div>
  );
}

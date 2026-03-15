"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchApprovals, approveAction, rejectAction } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { ApprovalList } from "@/components/approvals/approval-list";

export default function ApprovalsPage() {
  const queryClient = useQueryClient();

  const { data: approvals = [], isLoading } = useQuery({
    queryKey: ["approvals"],
    queryFn: fetchApprovals,
    refetchInterval: 15_000,
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
    <div className="p-6">
      <PageHeader title="Approvals" subtitle="Review and approve pending actions" />

      {isLoading ? (
        <p className="text-neutral-500 text-sm">Loading...</p>
      ) : (
        <ApprovalList
          approvals={approvals}
          onApprove={(id, reason) => approveMut.mutate({ id, reason })}
          onReject={(id, reason) => rejectMut.mutate({ id, reason })}
        />
      )}
    </div>
  );
}

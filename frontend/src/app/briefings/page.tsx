"use client";

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchBriefingList, fetchBriefingDetail, briefingAction } from "@/lib/api";
import { BriefingList } from "@/components/feature/briefings/briefing-list";
import { BriefingDetail } from "@/components/feature/briefings/briefing-detail";
import { EmptyState } from "@/components/ui/empty-state";

export default function BriefingsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // Fetch briefing list
  const { data: briefings, isLoading: listLoading } = useQuery({
    queryKey: ["briefings-list"],
    queryFn: () => fetchBriefingList(50),
  });

  // Fetch selected briefing detail
  const { data: detail } = useQuery({
    queryKey: ["briefing-detail", selectedId],
    queryFn: () => fetchBriefingDetail(selectedId!),
    enabled: !!selectedId,
  });

  // Lifecycle actions
  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: string }) => {
      return briefingAction(id, action);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["briefings-list"] });
      if (selectedId) {
        queryClient.invalidateQueries({ queryKey: ["briefing-detail", selectedId] });
      }
    },
  });

  const handleAction = useCallback(
    (action: string) => {
      if (selectedId) {
        actionMutation.mutate({ id: selectedId, action });
      }
    },
    [selectedId, actionMutation]
  );

  return (
    <div className="flex h-full">
      {/* List Pane */}
      <div className="w-80 shrink-0 border-r border-b-primary overflow-y-auto">
        <div className="p-3 border-b border-b-primary">
          <h2 className="text-sm font-medium text-t-primary">Briefings</h2>
        </div>
        {listLoading ? (
          <div className="p-4 text-sm text-t-tertiary">Loading...</div>
        ) : (
          <BriefingList
            briefings={briefings ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
      </div>

      {/* Detail Pane */}
      <div className="flex-1 overflow-y-auto">
        {detail ? (
          <BriefingDetail
            headline={detail.headline}
            fullText={detail.full_text}
            date={detail.date}
            confidence={detail.confidence}
            evidence={detail.evidence}
            relatedItems={detail.related_items ?? []}
            actions={detail.actions ?? []}
            onAction={handleAction}
          />
        ) : (
          <div className="flex items-center justify-center h-full">
            <EmptyState
              title="Select a briefing"
              description="Choose a briefing from the list to view its details"
            />
          </div>
        )}
      </div>
    </div>
  );
}

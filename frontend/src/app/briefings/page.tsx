"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchBriefing, fetchBriefingFeedback, submitBriefingFeedback } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { BriefingViewer } from "@/components/briefings/briefing-viewer";
import { FeedbackPanel } from "@/components/briefings/feedback-panel";
import { EmptyState } from "@/components/ui/empty-state";

function todayStr() {
  return new Date().toISOString().split("T")[0];
}

export default function BriefingsPage() {
  const [date, setDate] = useState(todayStr);

  const {
    data: briefing,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["briefing", date],
    queryFn: () => fetchBriefing(date),
    enabled: !!date,
  });

  const { data: feedbackSummary } = useQuery({
    queryKey: ["briefing-feedback", briefing?.briefing_id],
    queryFn: () => fetchBriefingFeedback(briefing!.briefing_id),
    enabled: !!briefing?.briefing_id,
  });

  const rateMut = useMutation({
    mutationFn: ({ briefingId, rating }: { briefingId: string; rating: number }) =>
      submitBriefingFeedback(briefingId, { feedback_type: "rating", rating }),
  });

  return (
    <div className="p-6">
      <PageHeader
        title="Briefings"
        subtitle="Daily briefing viewer"
        actions={
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          />
        }
      />

      {isLoading && <p className="text-neutral-500 text-sm">Loading briefing...</p>}

      {error && (
        <EmptyState title="No briefing found" description={`No briefing available for ${date}`} />
      )}

      {briefing && (
        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2">
            <BriefingViewer briefing={briefing} />
          </div>
          <div>
            <FeedbackPanel
              briefingId={briefing.briefing_id}
              summary={feedbackSummary}
              onRate={(briefingId, rating) => rateMut.mutate({ briefingId, rating })}
            />
          </div>
        </div>
      )}
    </div>
  );
}

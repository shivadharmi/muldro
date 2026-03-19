"use client";

import { useState } from "react";
import type { BriefingFeedbackSummary } from "@/lib/types";
import { Card, CardBody, CardHeader } from "@/components/ui/card";


export function FeedbackPanel({
  briefingId,
  summary,
  onRate,
}: {
  briefingId: string;
  summary: BriefingFeedbackSummary | undefined;
  onRate: (briefingId: string, rating: number) => void;
}) {
  const [selectedRating, setSelectedRating] = useState(0);

  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-medium">Feedback</span>
      </CardHeader>
      <CardBody>
        <div className="flex items-center gap-1 mb-3">
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              onClick={() => {
                setSelectedRating(star);
                onRate(briefingId, star);
              }}
              className={`text-lg transition-colors ${
                star <= selectedRating ? "text-yellow-400" : "text-neutral-700 hover:text-neutral-500"
              }`}
            >
              ★
            </button>
          ))}
          {selectedRating > 0 && (
            <span className="text-xs text-neutral-500 ml-2">Rated {selectedRating}/5</span>
          )}
        </div>

        {summary && (
          <div className="text-xs text-neutral-500 space-y-1">
            <p>Total feedback: {summary.total_feedback}</p>
            {summary.average_rating && <p>Average rating: {summary.average_rating.toFixed(1)}</p>}
            <p>Items acted on: {summary.items_acted_on}</p>
            <p>Items dismissed: {summary.items_dismissed}</p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

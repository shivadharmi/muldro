"use client";

import type { InsightData } from "@/lib/a2ui-types";
import { dismissInsight } from "@/lib/api";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { useState } from "react";

const sourceIcons: Record<string, string> = {
  gmail: "\u2709\uFE0F",
  github: "\uD83D\uDC19",
  calendar: "\uD83D\uDCC5",
  slack: "\uD83D\uDCAC",
  linear: "\uD83D\uDCCB",
};

interface InsightSurfaceProps {
  surfaceId: string;
  insightData: InsightData;
}

export function InsightSurface({ surfaceId, insightData }: InsightSurfaceProps) {
  const [dismissing, setDismissing] = useState(false);
  const [acting, setActing] = useState<number | null>(null);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleDismiss = async () => {
    setDismissing(true);
    try {
      await dismissInsight(surfaceId);
      removeSurface(surfaceId);
    } catch {
      setDismissing(false);
    }
  };

  const handleAction = (index: number) => {
    if (!sendAction) return;
    setActing(index);
    sendAction("execute_insight", {
      surface_id: surfaceId,
      action_index: index,
    });
  };

  const icon = sourceIcons[insightData.signal_source] ?? "\uD83D\uDD14";

  return (
    <div className="space-y-3">
      {/* Source badge */}
      <div className="flex items-center gap-2">
        <span className="text-base">{icon}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-400 font-medium uppercase tracking-wide">
          {insightData.signal_source}
        </span>
        {insightData.relevance_score >= 0.8 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium">
            High relevance
          </span>
        )}
      </div>

      {/* Signal summary */}
      <p className="text-sm text-t-primary font-medium">
        {insightData.signal_summary}
      </p>

      {/* Relevance reasoning */}
      {insightData.relevance_reasoning && (
        <p className="text-xs text-t-tertiary">
          {insightData.relevance_reasoning}
        </p>
      )}

      {/* Related goals */}
      {insightData.related_goals.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {insightData.related_goals.map((goal, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/15 text-blue-400"
            >
              {goal}
            </span>
          ))}
        </div>
      )}

      {/* Suggested actions */}
      {insightData.suggested_actions.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {insightData.suggested_actions.map((action, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleAction(i)}
              disabled={acting !== null}
              className="text-xs px-3 py-1.5 rounded-md bg-violet-500/20 text-violet-300 hover:bg-violet-500/30 transition-colors disabled:opacity-50"
            >
              {acting === i ? "Starting..." : action.description}
            </button>
          ))}
        </div>
      )}

      {/* Dismiss */}
      {insightData.dismiss_available && (
        <div className="pt-1 border-t border-b-primary">
          <button
            type="button"
            onClick={handleDismiss}
            disabled={dismissing}
            className="text-[10px] text-t-tertiary hover:text-t-secondary transition-colors disabled:opacity-50"
          >
            {dismissing ? "Dismissing..." : "Dismiss"}
          </button>
        </div>
      )}
    </div>
  );
}

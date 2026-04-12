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
      {/* 1. Signal summary — headline first */}
      <p className="text-sm text-t-primary font-semibold">
        {insightData.signal_summary}
      </p>

      {/* 2. Source + relevance — compact metadata line */}
      <div className="flex items-center gap-1.5 text-xs text-t-muted">
        <span>{icon}</span>
        <span>{insightData.signal_source}</span>
        {insightData.relevance_score >= 0.7 && (
          <>
            <span>&middot;</span>
            <span className="text-j-warning font-medium">High relevance</span>
          </>
        )}
      </div>

      {/* 3. Relevance reasoning */}
      {insightData.relevance_reasoning && (
        <p className="text-xs text-t-tertiary">
          {insightData.relevance_reasoning}
        </p>
      )}

      {/* 4. Related goals */}
      {insightData.related_goals.length > 0 && (
        <div>
          <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-1.5">Related goals</p>
          <div className="flex flex-wrap gap-1">
            {insightData.related_goals.map((goal, i) => (
              <span
                key={i}
                className="text-[10px] px-1.5 py-0.5 rounded-full bg-j-info-soft text-j-info"
              >
                {goal}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 5. Suggested actions + dismiss */}
      {(insightData.suggested_actions.length > 0 || insightData.dismiss_available) && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {insightData.suggested_actions.map((action, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleAction(i)}
              disabled={acting !== null}
              className={`text-xs px-3 py-1.5 rounded-[var(--radius-md)] transition-colors disabled:opacity-50 cursor-pointer ${
                i === 0
                  ? "bg-j-primary text-j-primary-fg font-medium hover:bg-j-primary-hover"
                  : "bg-surface-2 text-t-secondary hover:bg-surface-3"
              }`}
            >
              {acting === i ? "Starting..." : action.description}
            </button>
          ))}
          {insightData.dismiss_available && (
            <button
              type="button"
              onClick={handleDismiss}
              disabled={dismissing}
              className="text-xs text-t-muted hover:text-t-secondary transition-colors disabled:opacity-50 cursor-pointer ml-auto"
            >
              {dismissing ? "Dismissing..." : "Dismiss"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

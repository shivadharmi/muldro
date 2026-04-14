"use client";

import { useState, useCallback } from "react";
import type { InsightData } from "@/lib/a2ui-types";
import { dismissInsight } from "@/lib/api";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { Tooltip } from "@/components/ui/tooltip";
import { Modal } from "@/components/ui/modal";

const sourceIcons: Record<string, string> = {
  gmail: "\u2709\uFE0F",
  github: "\uD83D\uDC19",
  calendar: "\uD83D\uDCC5",
  slack: "\uD83D\uDCAC",
  notion: "\uD83D\uDCDD",
  jira: "\uD83D\uDD37",
};

interface InsightSurfaceProps {
  surfaceId: string;
  insightData: InsightData;
}

export function InsightSurface({ surfaceId, insightData }: InsightSurfaceProps) {
  const [dismissing, setDismissing] = useState(false);
  const [acting, setActing] = useState<number | null>(null);
  const [showDismissConfirm, setShowDismissConfirm] = useState(false);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleDismissConfirm = useCallback(async () => {
    setShowDismissConfirm(false);
    setDismissing(true);
    try {
      await dismissInsight(surfaceId);
      removeSurface(surfaceId);
    } catch {
      setDismissing(false);
    }
  }, [surfaceId, removeSurface]);

  const handleAction = useCallback(
    (index: number) => {
      if (!sendAction) return;
      setActing(index);
      sendAction("execute_insight", {
        surface_id: surfaceId,
        action_index: index,
      });
    },
    [sendAction, surfaceId],
  );

  const icon = sourceIcons[insightData.signal_source] ?? "\uD83D\uDD14";

  return (
    <>
      <div className="space-y-3">
        {/* 1. Signal summary */}
        <p className="text-sm text-t-primary font-semibold">
          {insightData.signal_summary}
        </p>

        {/* 2. Source + relevance */}
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
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-1.5">
              Related goals
            </p>
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
              <Tooltip
                key={i}
                text={action.action_preview || `Execute: ${action.description}`}
              >
                <button
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
              </Tooltip>
            ))}
            {insightData.dismiss_available && (
              <button
                type="button"
                onClick={() => setShowDismissConfirm(true)}
                disabled={dismissing}
                className="text-xs text-t-muted hover:text-t-secondary transition-colors disabled:opacity-50 cursor-pointer ml-auto"
              >
                {dismissing ? "Dismissing..." : "Dismiss"}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Dismiss confirmation modal */}
      <Modal
        open={showDismissConfirm}
        onClose={() => setShowDismissConfirm(false)}
        title="Dismiss this insight?"
        size="sm"
      >
        <div className="space-y-3">
          <p className="text-sm text-t-secondary">
            This insight will be removed from your workspace.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setShowDismissConfirm(false)}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDismissConfirm}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer"
            >
              Yes, Dismiss
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}

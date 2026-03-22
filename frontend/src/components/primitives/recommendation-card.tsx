"use client";

import Link from "next/link";

interface Props {
  actionType: string;
  title: string;
  description: string;
  reasoning?: string;
  impact?: string;
  confidence?: number;
  priority?: string;
  actionUrl?: string;
  onDismiss?: () => void;
}

const PRIORITY_STYLES: Record<string, { border: string; icon: string }> = {
  critical: { border: "border-l-red-500", icon: "!!" },
  high: { border: "border-l-orange-500", icon: "!" },
  medium: { border: "border-l-yellow-500", icon: "~" },
  low: { border: "border-l-neutral-500", icon: "" },
};

const ACTION_ICONS: Record<string, string> = {
  review_approvals: "shield",
  unblock_runs: "play",
  investigate_failures: "search",
  fix_observations: "link",
};

export function RecommendationCard({
  actionType,
  title,
  description,
  reasoning,
  impact,
  confidence,
  priority,
  actionUrl,
  onDismiss,
}: Props) {
  const style = PRIORITY_STYLES[priority || "medium"] || PRIORITY_STYLES.medium;
  const expanded = priority === "critical" || priority === "high";

  const content = (
    <div className={`rounded-lg border border-b-primary ${style.border} border-l-[3px] bg-surface-0 p-3 hover:bg-surface-1 transition-colors`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Header: priority + type + confidence */}
          <div className="flex items-center gap-2 mb-1">
            {style.icon && (
              <span className={`text-[10px] font-bold uppercase ${
                priority === "critical" ? "text-red-400" : "text-orange-400"
              }`}>
                {priority}
              </span>
            )}
            <span className="text-[10px] text-t-tertiary uppercase tracking-wider">
              {actionType.replace(/_/g, " ")}
            </span>
            {confidence != null && (
              <div className="flex items-center gap-1">
                <div className="w-12 h-1 rounded-full bg-surface-2 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent-primary"
                    style={{ width: `${Math.round(confidence * 100)}%` }}
                  />
                </div>
                <span className="text-[10px] text-t-tertiary">
                  {Math.round(confidence * 100)}%
                </span>
              </div>
            )}
          </div>

          {/* Title */}
          <h4 className="text-sm font-medium text-t-primary">{title}</h4>

          {/* Description */}
          <p className="text-xs text-t-secondary mt-0.5">{description}</p>

          {/* Reasoning (expanded for high/critical priority) */}
          {reasoning && expanded && (
            <p className="text-xs text-t-tertiary mt-1.5 leading-relaxed">
              {reasoning}
            </p>
          )}

          {/* Impact */}
          {impact && expanded && (
            <div className="mt-1.5 flex items-start gap-1.5">
              <span className="text-[10px] text-orange-400 font-medium shrink-0 mt-px">Impact:</span>
              <span className="text-[10px] text-t-tertiary">{impact}</span>
            </div>
          )}
        </div>

        {/* Action button */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          {actionUrl && (
            <span className="px-3 py-1.5 rounded-md bg-accent-primary text-white text-xs font-medium">
              View
            </span>
          )}
          {onDismiss && (
            <button
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDismiss(); }}
              className="px-2 py-1 text-[10px] text-t-tertiary hover:text-t-secondary cursor-pointer"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  );

  if (actionUrl) {
    return <Link href={actionUrl}>{content}</Link>;
  }
  return content;
}

"use client";

import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";

interface Props {
  actionType: string;
  title: string;
  description: string;
  confidence?: number;
  priority?: string;
  evidenceCount?: number;
  onAct?: () => void;
  onDismiss?: () => void;
}

export function RecommendationCard({
  actionType,
  title,
  description,
  confidence,
  priority,
  evidenceCount = 0,
  onAct,
  onDismiss,
}: Props) {
  return (
    <div className="rounded-[var(--radius-md)] border border-b-primary bg-surface-0 p-3">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs text-t-tertiary uppercase tracking-wider">
              {actionType}
            </span>
            {confidence != null && (
              <span className="text-xs text-t-tertiary">
                {Math.round(confidence * 100)}% confident
              </span>
            )}
            {priority && !confidence && (
              <span className={`text-xs font-medium ${
                priority === "high" ? "text-status-error" : "text-t-tertiary"
              }`}>
                {priority}
              </span>
            )}
          </div>
          <h4 className="text-sm font-medium text-t-primary">{title}</h4>
          <div className="text-sm text-t-secondary mt-0.5">
            <InlineMarkdown content={description} />
          </div>
          {evidenceCount > 0 && (
            <p className="text-xs text-t-tertiary mt-1">
              Based on {evidenceCount} evidence source{evidenceCount > 1 ? "s" : ""}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1">
          {onAct && (
            <button
              onClick={onAct}
              className="px-3 py-1.5 rounded-[var(--radius-sm)] bg-accent-primary text-white text-xs font-medium hover:opacity-90 transition-opacity cursor-pointer"
            >
              Act
            </button>
          )}
          {onDismiss && (
            <button
              onClick={onDismiss}
              className="px-3 py-1.5 rounded-[var(--radius-sm)] text-t-tertiary text-xs hover:text-t-secondary transition-colors cursor-pointer"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

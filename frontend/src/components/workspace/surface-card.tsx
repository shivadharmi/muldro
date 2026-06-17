"use client";

import type { WorkspaceSurface } from "@/stores/surface-store";
import { StepListCompact } from "@/components/a2ui/components/step-list";
import { InsightSurface } from "@/components/a2ui/components/insight-surface";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import type { InsightData } from "@/lib/a2ui-types";
import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";

interface Props {
  surface: WorkspaceSurface;
  onClick: () => void;
}

const kindLabel: Record<string, string> = {
  run: "Run",
  summary: "Summary",
  message: "Message",
  briefing: "Briefing",
  alert: "Alert",
  recommendation: "Rec",
  proactive_insight: "Insight",
  // Legacy
  plan: "Plan",
  approval: "Approval",
  execution: "Execution",
  checklist: "Checklist",
  comparison: "Compare",
  timeline: "Timeline",
  table: "Table",
  activity: "Activity",
};

const kindColor: Record<string, string> = {
  run: "bg-j-info-soft text-j-info",
  summary: "bg-j-success-soft text-j-success",
  message: "bg-j-secondary-soft text-j-secondary",
  briefing: "bg-j-success-soft text-j-success",
  alert: "bg-j-error-soft text-j-error",
  recommendation: "bg-j-secondary-soft text-j-secondary",
  proactive_insight: "bg-j-secondary-soft text-j-secondary",
  // Legacy
  plan: "bg-j-info-soft text-j-info",
  approval: "bg-j-warning-soft text-j-warning",
  execution: "bg-j-info-soft text-j-info",
};

const statusDotColor: Record<string, string> = {
  pending: "bg-t-muted",
  running: "bg-j-info animate-pulse-live",
  completed: "bg-j-success",
  failed: "bg-j-error",
  awaiting_approval: "bg-j-warning animate-pulse-live",
  cancelled: "bg-t-muted",
  proposal: "bg-j-secondary animate-pulse-live",
};

const phaseDotColor: Record<string, string> = {
  planning: "bg-j-info animate-pulse-live",
  plan_ready: "bg-j-info",
  executing: "bg-j-info animate-pulse-live",
  approval_needed: "bg-j-warning animate-pulse-live",
  completed: "bg-j-success",
  failed: "bg-j-error",
  partial: "bg-j-warning",
  proposal: "bg-j-secondary animate-pulse-live",
};

const priorityBadge: Record<string, string> = {
  low: "bg-surface-3 text-t-secondary",
  medium: "bg-j-info-soft text-j-info",
  high: "bg-j-warning-soft text-j-warning",
  critical: "bg-j-error-soft text-j-error",
};

const metricVariantClass: Record<string, string> = {
  default: "text-t-secondary",
  success: "text-j-success",
  warning: "text-j-warning",
  danger: "text-j-error",
};

// Cap how many sections are rendered inline on the card so oversized
// Presenter payloads don't blow out the grid. Full content shows in the modal.
const MAX_INLINE_SECTIONS = 3;

export function SurfaceCard({ surface, onClick }: Props) {
  const { preview, kind } = surface;
  const inlineSections = (surface.surface_data?.sections ?? []).slice(
    0,
    MAX_INLINE_SECTIONS,
  );
  const hiddenSectionCount = Math.max(
    0,
    (surface.surface_data?.sections?.length ?? 0) - MAX_INLINE_SECTIONS,
  );

  // The card body renders nested interactive content (A2UIRenderer buttons/forms,
  // InsightSurface controls), so the clickable root must be a div with a button role —
  // a real <button> wrapping <button> is invalid DOM (hydration warnings, dropped
  // clicks). The keydown guard only activates when the card itself is focused, leaving
  // nested controls to handle their own Enter/Space.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      className="w-full text-left rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 p-4 surface-card cursor-pointer group"
    >
      {/* Header: kind badge + priority */}
      <div className="flex items-center gap-2 mb-2.5">
        <span className={`text-[10px] font-medium px-2 py-0.5 rounded-[var(--radius-sm)] ${kindColor[kind] ?? "bg-surface-3 text-t-secondary"}`}>
          {kindLabel[kind] ?? kind}
        </span>
        {(surface.phase || preview.status) && (
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${
              surface.phase
                ? phaseDotColor[surface.phase] ?? "bg-t-muted"
                : statusDotColor[preview.status!] ?? "bg-t-muted"
            }`}
          />
        )}
        <div className="flex-1" />
        {preview.priority && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] font-medium ${priorityBadge[preview.priority] ?? ""}`}
          >
            {preview.priority}
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className="text-[13px] font-medium text-t-primary line-clamp-2 mb-1 leading-snug">
        <InlineMarkdown content={preview.title} />
      </h3>

      {/* Subtitle */}
      {preview.subtitle && (
        <p className="text-xs text-t-tertiary line-clamp-2 mb-2.5 leading-relaxed">
          <InlineMarkdown content={preview.subtitle} />
        </p>
      )}

      {/* Insight surface content */}
      {kind === "proactive_insight" && surface.insight_data && (
        <div className="mb-2.5" onClick={(e) => e.stopPropagation()}>
          <InsightSurface
            surfaceId={surface.id}
            insightData={surface.insight_data as unknown as InsightData}
          />
        </div>
      )}

      {/* Execution step count */}
      {surface.steps && surface.steps.length > 0 && (
        <div className="mb-2.5">
          <StepListCompact steps={surface.steps} />
        </div>
      )}

      {/* Progress bar */}
      {preview.progress != null && (
        <div className="w-full h-1 bg-surface-3 rounded-full mb-2.5">
          <div
            className="h-full bg-j-primary rounded-full transition-all duration-300"
            style={{ width: `${Math.min(preview.progress * 100, 100)}%` }}
          />
        </div>
      )}

      {/* Metrics row */}
      {preview.metrics.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2.5">
          {preview.metrics.slice(0, 4).map((m, i) => (
            <span key={i} className="text-[11px]">
              <span className="text-t-muted">{m.label} </span>
              <span className={metricVariantClass[m.variant] ?? "text-t-secondary"}>
                {m.value}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Entities row */}
      {preview.entities.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2.5">
          {preview.entities.slice(0, 3).map((e, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-secondary"
            >
              {e}
            </span>
          ))}
          {preview.entities.length > 3 && (
            <span className="text-[10px] text-t-muted">
              +{preview.entities.length - 3}
            </span>
          )}
        </div>
      )}

      {/* Tags */}
      {preview.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2.5">
          {preview.tags.slice(0, 5).map((t, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-j-primary-soft text-j-primary"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Inline surface_data preview — first few typed A2UI sections.
          Clicks bubble to the card's open-modal handler; any action fired
          by a nested interactive component routes to the modal so the full
          view is shown rather than executing the action from a preview. */}
      {inlineSections.length > 0 && (
        <div className="mb-2.5 max-h-[280px] overflow-hidden rounded-[var(--radius-md)] border border-b-secondary/60 bg-surface-2/40 p-2.5">
          <A2UIRenderer
            surface={{
              type: "surface",
              id: `card-${surface.id}`,
              children: inlineSections,
              metadata: {},
            }}
            onAction={() => onClick()}
          />
          {hiddenSectionCount > 0 && (
            <p className="mt-1.5 text-[10px] text-t-muted">
              +{hiddenSectionCount} more section{hiddenSectionCount === 1 ? "" : "s"} — click to expand
            </p>
          )}
        </div>
      )}

      {/* Footer: timestamp + arrow */}
      <div className="flex items-center justify-between mt-1 pt-2 border-t border-b-secondary">
        {preview.timestamp ? (
          <span className="text-[10px] text-t-muted">
            {formatRelativeTime(preview.timestamp)}
          </span>
        ) : <span />}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          className="text-t-muted group-hover:text-t-secondary group-hover:translate-x-0.5 transition-all duration-150"
        >
          <path
            d="M9 18l6-6-6-6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}

function formatRelativeTime(isoTimestamp: string): string {
  const now = Date.now();
  const then = new Date(isoTimestamp).getTime();
  const diffMs = now - then;

  if (diffMs < 60_000) return "just now";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  return `${Math.floor(diffMs / 86_400_000)}d ago`;
}

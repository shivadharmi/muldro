"use client";

import type { WorkspaceSurface } from "@/stores/surface-store";

interface Props {
  surface: WorkspaceSurface;
  onClick: () => void;
}

const kindBorderColor: Record<string, string> = {
  plan: "border-l-blue-500",
  approval: "border-l-amber-500",
  briefing: "border-l-green-500",
  alert: "border-l-red-500",
  summary: "border-l-gray-400",
  recommendation: "border-l-gray-400",
};

const statusDotColor: Record<string, string> = {
  pending: "bg-gray-400",
  running: "bg-blue-400 animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  awaiting_approval: "bg-amber-400",
  cancelled: "bg-gray-500",
};

const priorityBadge: Record<string, string> = {
  low: "bg-gray-500/20 text-gray-400",
  medium: "bg-blue-500/20 text-blue-400",
  high: "bg-amber-500/20 text-amber-400",
  critical: "bg-red-500/20 text-red-400",
};

const metricVariantClass: Record<string, string> = {
  default: "text-t-secondary",
  success: "text-green-400",
  warning: "text-amber-400",
  danger: "text-red-400",
};

export function SurfaceCard({ surface, onClick }: Props) {
  const { preview, kind } = surface;
  const border = kindBorderColor[kind] ?? "border-l-gray-400";

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left rounded-lg border border-b-primary border-l-4 ${border} bg-surface-0 p-4 hover:bg-surface-1 transition-colors cursor-pointer`}
    >
      {/* Header: status dot + title + priority */}
      <div className="flex items-start gap-2 mb-1">
        {preview.status && (
          <span
            className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${statusDotColor[preview.status] ?? "bg-gray-400"}`}
          />
        )}
        <h3 className="text-sm font-medium text-t-primary flex-1 line-clamp-2">
          {preview.title}
        </h3>
        {preview.priority && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${priorityBadge[preview.priority] ?? ""}`}
          >
            {preview.priority}
          </span>
        )}
      </div>

      {/* Subtitle */}
      {preview.subtitle && (
        <p className="text-xs text-t-tertiary line-clamp-2 mb-2">
          {preview.subtitle}
        </p>
      )}

      {/* Progress bar */}
      {preview.progress != null && (
        <div className="w-full h-1.5 bg-surface-2 rounded-full mb-2">
          <div
            className="h-full bg-blue-500 rounded-full transition-all"
            style={{ width: `${Math.min(preview.progress * 100, 100)}%` }}
          />
        </div>
      )}

      {/* Metrics row */}
      {preview.metrics.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2">
          {preview.metrics.slice(0, 4).map((m, i) => (
            <span key={i} className="text-xs">
              <span className="text-t-tertiary">{m.label}: </span>
              <span className={metricVariantClass[m.variant] ?? "text-t-secondary"}>
                {m.value}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Entities row */}
      {preview.entities.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {preview.entities.slice(0, 3).map((e, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-2 text-t-secondary"
            >
              {e}
            </span>
          ))}
          {preview.entities.length > 3 && (
            <span className="text-[10px] text-t-tertiary">
              +{preview.entities.length - 3} more
            </span>
          )}
        </div>
      )}

      {/* Tags */}
      {preview.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {preview.tags.slice(0, 5).map((t, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded bg-accent-primary/10 text-accent-primary"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Footer: timestamp + click affordance */}
      <div className="flex items-center justify-between mt-1">
        {preview.timestamp && (
          <span className="text-[10px] text-t-tertiary">
            {formatRelativeTime(preview.timestamp)}
          </span>
        )}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          className="text-t-tertiary ml-auto"
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
    </button>
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

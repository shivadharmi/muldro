"use client";

import { useState } from "react";
import type { TrustDashboardEntry } from "@/lib/types";
import { humaniseSlug } from "../labels";
import { TRUST_LEVEL_COLORS, TRUST_LEVEL_LABELS, CEILING_OPTIONS } from "./trust-constants";

interface TrustCapabilityCardProps {
  entry: TrustDashboardEntry;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingDisabled?: boolean;
  resetDisabled?: boolean;
}

export function TrustCapabilityCard({
  entry,
  onCeilingChange,
  onReset,
  ceilingDisabled,
  resetDisabled,
}: TrustCapabilityCardProps) {
  const [expanded, setExpanded] = useState(false);

  const bestProgress = entry.risk_levels.reduce((best, rl) => {
    const pct = rl.graduation_progress?.percentage ?? 0;
    return pct > best ? pct : best;
  }, 0);

  return (
    <div className="rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 overflow-hidden">
      <div className="px-4 py-3 space-y-2">
        {/* Header row */}
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-between text-left cursor-pointer group"
        >
          <div className="flex items-center gap-2.5">
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-t-muted"}`}
            />
            <span className="text-[13px] font-medium text-t-primary">
              {entry.capability}
            </span>
            <span className="text-[11px] text-t-muted px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2">
              {TRUST_LEVEL_LABELS[entry.trust_level] ?? humaniseSlug(entry.trust_level)}
            </span>
          </div>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            className={`text-t-muted group-hover:text-t-secondary transition-all duration-150 ${expanded ? "rotate-90" : ""}`}
          >
            <path
              d="M9 18l6-6-6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {/* Graduation progress bar */}
        {entry.trust_level !== "autonomous" && (
          <div className="w-full h-1 bg-surface-3 rounded-full">
            <div
              className={`h-full rounded-full transition-all duration-300 ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-t-muted"}`}
              style={{
                width: `${Math.min(bestProgress * 100, 100)}%`,
              }}
            />
          </div>
        )}
      </div>

      {/* Expanded: per-risk breakdown + controls */}
      {expanded && (
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-b-secondary">
          {entry.risk_levels.map((rl) => (
            <div
              key={rl.risk_level}
              className="flex items-center justify-between text-xs"
            >
              <span className="text-t-secondary w-16 capitalize">
                {humaniseSlug(rl.risk_level)}
              </span>
              <span
                className={`px-1.5 py-0.5 rounded-[var(--radius-sm)] ${TRUST_LEVEL_COLORS[rl.trust_level] ?? "bg-t-muted"} text-white text-[10px] font-medium`}
              >
                {TRUST_LEVEL_LABELS[rl.trust_level] ?? humaniseSlug(rl.trust_level)}
              </span>
              <span className="text-t-tertiary">
                {rl.approved_count}
                <span className="text-t-muted"> approved</span>
                {rl.rejected_count > 0 && (
                  <span className="text-j-error ml-1">
                    {rl.rejected_count} rejected
                  </span>
                )}
              </span>
              {rl.graduation_progress?.next_level && (
                <span className="text-t-muted text-[10px]">
                  {rl.graduation_progress.current}/
                  {rl.graduation_progress.target} to{" "}
                  {TRUST_LEVEL_LABELS[
                    rl.graduation_progress.next_level
                  ] ?? rl.graduation_progress.next_level}
                </span>
              )}
            </div>
          ))}

          {/* Ceiling control */}
          <div className="flex items-center gap-2 pt-2 border-t border-b-secondary">
            <label className="text-[11px] text-t-muted font-medium">Ceiling</label>
            <select
              value={entry.ceiling}
              onChange={(e) =>
                onCeilingChange(entry.capability, e.target.value)
              }
              disabled={ceilingDisabled}
              className="text-xs rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-2.5 py-1.5 text-t-primary disabled:opacity-50 cursor-pointer"
            >
              {CEILING_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>

            <button
              onClick={() => onReset(entry.capability)}
              disabled={resetDisabled}
              className="ml-auto text-xs text-j-error hover:text-j-error/80 font-medium disabled:opacity-50 cursor-pointer"
            >
              {resetDisabled ? "Resetting..." : "Reset"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import { statusTextColor } from "@/lib/design-tokens";

// ── Shared step-rendering primitives ───────────────────────────────────────────
//
// Single source of truth for how a "step" looks across the three surfaces that
// render steps: the live execution StepList, the persisted run-detail Steps tab,
// and the A2UI ExecutionTrace timeline. Owning the status-icon mapping and the
// duration formatter here keeps those three visually consistent and on the
// Jarvis design tokens (no hardcoded hex).

export interface StepIcon {
  icon: string;
  className: string;
}

/**
 * Maps any step status (live, persisted, or trace shape) to a glyph + token
 * text-color class. Statuses that mean the same thing collapse to one look:
 *   running/executing → ◉ info (pulsing)
 *   completed/ok      → ✓ success
 *   failed/error      → ✗ error
 *   waiting_approval/approval_needed → ⚠ warning
 *   user_action       → 👤 secondary
 *   skipped           → — tertiary
 *   timed_out         → ⏱ warning
 *   pending/ready/default → ○ tertiary
 */
export function stepStatusIcon(status: string): StepIcon {
  switch (status) {
    case "running":
    case "executing":
    case "in_progress":
      return { icon: "◉", className: `${statusTextColor("running")} animate-pulse` };
    case "completed":
    case "ok":
      return { icon: "✓", className: statusTextColor("completed") };
    case "failed":
    case "error":
      return { icon: "✗", className: statusTextColor("failed") };
    case "waiting_approval":
    case "approval_needed":
    case "awaiting_approval":
      return { icon: "⚠", className: statusTextColor("awaiting_approval") };
    case "user_action":
      return { icon: "👤", className: statusTextColor("user_action") };
    case "skipped":
      return { icon: "—", className: "text-t-tertiary" };
    case "timed_out":
      return { icon: "⏱", className: statusTextColor("awaiting_approval") };
    case "ready":
      return { icon: "○", className: "text-t-tertiary" };
    case "pending":
    default:
      return { icon: "○", className: "text-t-tertiary opacity-60" };
  }
}

/** Formats a millisecond duration as a compact human string (e.g. 850ms, 1.2s, 2m 5s). */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

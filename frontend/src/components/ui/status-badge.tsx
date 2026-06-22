import { statusColor, statusLabel } from "@/lib/design-tokens";

interface StatusBadgeProps {
  /** Execution/task status, e.g. "running", "completed", "awaiting_approval", "proposal" */
  status: string;
  /** Optional label override (defaults to the canonical Title-case status label) */
  label?: string;
}

/** A status indicator pill: colored dot (keyed by status) + Title-case label.
 *  Replaces bare status dots so surfaces read as "Running", "Completed", etc.
 *  All colors are sourced from design-tokens (statusColor) — no hardcoded hex. */
export function StatusBadge({ status, label }: StatusBadgeProps) {
  const dotClass = statusColor(status);
  const pulse =
    status === "running" ||
    status === "executing" ||
    status === "in_progress" ||
    status === "awaiting_approval" ||
    status === "proposal";

  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-medium text-t-secondary">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${dotClass} ${pulse ? "animate-pulse-live" : ""}`}
        aria-hidden="true"
      />
      {label ?? statusLabel(status)}
    </span>
  );
}

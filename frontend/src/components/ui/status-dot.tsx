import { statusColor, phaseBgColor } from "@/lib/design-tokens";

interface StatusDotProps {
  /** Uses statusColor() to derive bg class */
  status?: string;
  /** Uses phaseBgColor() to derive bg class + pulse animation */
  phase?: string;
  /** Direct Tailwind bg class override (e.g. "bg-j-success") */
  color?: string;
  /** sm = w-1.5 h-1.5, md = w-2 h-2. Default: md */
  size?: "sm" | "md";
}

/** Tiny colored dot for status/phase indicators. Replaces inline <span> patterns. */
export function StatusDot({ status, phase, color, size = "md" }: StatusDotProps) {
  let bgClass: string;
  let pulse = false;

  if (phase) {
    const p = phaseBgColor(phase);
    bgClass = p.className;
    pulse = p.pulse;
  } else if (status) {
    bgClass = statusColor(status);
    pulse = status === "running" || status === "executing" || status === "in_progress";
  } else {
    bgClass = color ?? "bg-t-muted";
  }

  const sizeClass = size === "sm" ? "w-1.5 h-1.5" : "w-2 h-2";

  return (
    <span
      className={`inline-block rounded-full shrink-0 ${sizeClass} ${bgClass} ${pulse ? "animate-pulse-live" : ""}`}
      aria-hidden="true"
    />
  );
}

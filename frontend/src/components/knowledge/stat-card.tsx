"use client";

// ── Stat Card ─────────────────────────────────────────────────────
// A single metric card with label, value, and optional weekly delta.

interface StatCardProps {
  label: string;
  value: string | number;
  delta?: number;
  color?: string; // Tailwind text color class like "text-j-primary"
}

export function StatCard({
  label,
  value,
  delta,
  color = "text-t-primary",
}: StatCardProps) {
  return (
    <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] px-3 sm:px-4 py-2.5 sm:py-3">
      <p className="text-[11px] text-t-muted uppercase tracking-wider truncate">
        {label}
      </p>
      <p className={`text-xl sm:text-2xl font-bold mt-1 ${color}`}>{value}</p>
      {delta !== undefined && delta !== 0 && (
        <p
          className={`text-xs mt-1 ${
            delta > 0 ? "text-j-accent" : "text-j-error"
          }`}
        >
          {delta > 0 ? `\u2191 +${delta}` : `\u2193 ${delta}`} this week
        </p>
      )}
    </div>
  );
}

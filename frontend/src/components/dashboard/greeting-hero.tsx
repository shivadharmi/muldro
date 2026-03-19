"use client";

import Link from "next/link";

export function GreetingHero({
  headline,
  briefingId,
  approvalCount,
  sourceCount,
}: {
  headline: string | null;
  briefingId: string | null;
  approvalCount: number;
  sourceCount: number;
}) {
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="rounded-[var(--radius-lg)] p-6 bg-surface-1 border border-b-secondary relative overflow-hidden">
      <div
        className="absolute inset-0 opacity-[0.04] pointer-events-none"
        style={{
          background:
            "linear-gradient(135deg, var(--jarvis-primary), var(--jarvis-secondary))",
        }}
      />
      <div className="relative">
        <p className="text-xs text-t-muted mb-1">{today}</p>
        <h2 className="text-xl font-semibold text-t-primary mb-2">{greeting}</h2>
        {headline && (
          <p className="text-sm text-t-secondary mb-4 max-w-xl">{headline}</p>
        )}

        <div className="flex flex-wrap gap-2 mb-4">
          <Link
            href="/chat"
            className="px-3 py-1.5 bg-j-primary text-j-primary-fg text-xs font-medium rounded-[var(--radius-md)] hover:bg-j-primary-hover transition-colors"
          >
            Talk to Jarvis
          </Link>
          {briefingId && (
            <Link
              href="/briefings"
              className="px-3 py-1.5 bg-surface-2 text-t-secondary text-xs font-medium rounded-[var(--radius-md)] hover:bg-surface-3 transition-colors"
            >
              View Briefing
            </Link>
          )}
          {approvalCount > 0 && (
            <Link
              href="/approvals"
              className="px-3 py-1.5 bg-j-warning-soft text-j-warning text-xs font-medium rounded-[var(--radius-md)] hover:opacity-80 transition-opacity"
            >
              Approvals ({approvalCount})
            </Link>
          )}
        </div>

        <div className="flex items-center gap-1.5 text-[11px] text-t-muted">
          <span className="w-1.5 h-1.5 rounded-full bg-j-primary animate-pulse-live" />
          Jarvis is monitoring {sourceCount} source{sourceCount !== 1 ? "s" : ""}
        </div>
      </div>
    </div>
  );
}

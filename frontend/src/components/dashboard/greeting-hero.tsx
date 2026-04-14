"use client";

import Link from "next/link";

export function GreetingHero({
  headline,
  approvalCount,
  sourceCount,
}: {
  headline: string | null;
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
    <div className="rounded-[var(--radius-xl)] p-6 sm:p-8 bg-surface-1 border border-b-secondary relative overflow-hidden noise-bg">
      {/* Atmospheric gradient — stronger presence */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 10% 40%, hsl(193 100% 62% / 0.06), transparent 60%), radial-gradient(ellipse 60% 40% at 90% 80%, hsl(247 80% 72% / 0.04), transparent 50%)",
        }}
      />
      <div className="relative">
        <p className="text-[11px] text-t-muted font-medium tracking-wide uppercase mb-2">{today}</p>
        <h2 className="text-2xl sm:text-3xl font-semibold text-t-primary mb-1 tracking-tight">{greeting}</h2>
        {headline && (
          <p className="text-sm text-t-secondary mb-5 max-w-xl leading-relaxed">{headline}</p>
        )}
        {!headline && <div className="mb-5" />}

        <div className="flex flex-wrap items-center gap-3">
          <Link
            href="/chat"
            className="inline-flex items-center gap-2 px-4 py-2 bg-j-primary text-j-primary-fg text-[13px] font-medium rounded-[var(--radius-md)] hover:bg-j-primary-hover transition-all duration-150 shadow-[var(--shadow-sm)]"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M3 3h10v7H6l-3 3V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
            </svg>
            Talk to Jarvis
          </Link>
          {approvalCount > 0 && (
            <Link
              href="/settings"
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-j-warning-soft text-j-warning text-[13px] font-medium rounded-[var(--radius-md)] hover:bg-j-warning/20 transition-colors"
            >
              <span className="w-2 h-2 rounded-full bg-j-warning animate-pulse-live" />
              {approvalCount} pending
            </Link>
          )}

          <div className="flex items-center gap-1.5 text-[11px] text-t-muted ml-1">
            <span className="w-1.5 h-1.5 rounded-full bg-j-success animate-pulse-live" />
            {sourceCount > 0
              ? `Monitoring ${sourceCount} source${sourceCount !== 1 ? "s" : ""}`
              : "No sources connected"}
          </div>
        </div>
      </div>
    </div>
  );
}

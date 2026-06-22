"use client";

import Link from "next/link";

/**
 * Shown on the dashboard when no briefing surface exists yet. Because the
 * morning-briefing schedule is enabled at workspace creation, a brand-new user
 * should see that Jarvis is already working — assembling their first briefing —
 * rather than a "nothing here" empty state. Connecting a source enriches it.
 */
export function BriefingGatheringCard() {
  return (
    <div className="rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-8 sm:p-10">
      <div className="flex flex-col items-center text-center max-w-md mx-auto">
        <div className="flex items-center gap-1.5 mb-4" aria-hidden>
          <span className="w-2 h-2 rounded-full bg-j-primary animate-pulse" />
          <span
            className="w-2 h-2 rounded-full bg-j-primary animate-pulse"
            style={{ animationDelay: "150ms" }}
          />
          <span
            className="w-2 h-2 rounded-full bg-j-primary animate-pulse"
            style={{ animationDelay: "300ms" }}
          />
        </div>
        <p className="text-[15px] text-t-primary font-medium mb-1">
          Gathering data for your first briefing
        </p>
        <p className="text-sm text-t-tertiary leading-relaxed mb-6">
          Jarvis is getting set up and will prepare your daily briefing here.
          Connect a source to make it sharper — until then it draws on whatever
          it can reach.
        </p>
        <div className="flex gap-3">
          <Link
            href="/integrations"
            className="px-4 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover transition-colors shadow-[var(--shadow-sm)]"
          >
            Connect Sources
          </Link>
          <Link
            href="/chat"
            className="px-4 py-2 rounded-[var(--radius-md)] border border-b-secondary text-t-secondary text-[13px] hover:bg-surface-2 transition-colors"
          >
            Talk to Jarvis
          </Link>
        </div>
      </div>
    </div>
  );
}

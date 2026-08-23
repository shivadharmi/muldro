"use client";

import Link from "next/link";

/**
 * Shown when sources are connected but no briefing exists yet.
 *
 * This card is only reachable with at least one source connected — with none,
 * `resolveFirstRunState` returns `onboarding` and the OnboardingCard renders
 * instead. So its old copy ("Connect a source to make it sharper", above a
 * primary "Connect Sources" button) could never be true at the moment it was
 * shown: it told a founder who had just connected Gmail to go and connect
 * something, while their mail sat in cards directly beneath it.
 *
 * What is actually true here is that muldro is already watching and the
 * briefing runs on a schedule, so that is what it says.
 */
export function BriefingGatheringCard({ sourceCount }: { sourceCount: number }) {
  const sources =
    sourceCount === 1 ? "one source" : `${sourceCount} sources`;

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
          Your first briefing is being assembled
        </p>
        <p className="text-sm text-t-tertiary leading-relaxed mb-6">
          Muldro is watching {sources} and will write your briefing here on its
          daily schedule. Anything it finds before then appears below as it
          arrives.
        </p>
        <div className="flex gap-3">
          <Link
            href="/chat"
            className="px-4 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover transition-colors shadow-[var(--shadow-sm)]"
          >
            Talk to Muldro
          </Link>
          <Link
            href="/integrations"
            className="px-4 py-2 rounded-[var(--radius-md)] border border-b-secondary text-t-secondary text-[13px] hover:bg-surface-2 transition-colors"
          >
            Add another source
          </Link>
        </div>
      </div>
    </div>
  );
}

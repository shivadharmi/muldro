"use client";

import { useState } from "react";
import Link from "next/link";
import { getAuthUrl } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { GoogleLogo, GitHubLogo, NotionLogo } from "@/components/integrations/logos";

type LogoComponent = React.FC<{ className?: string }>;

interface PrimarySource {
  provider: string;
  label: string;
  hint: string;
  Logo: LogoComponent;
}

// OAuth slugs must match the backend authorize route (routes_auth.py):
// google, github, notion. Slack has no authorize branch and is excluded.
const PRIMARY_SOURCES: PrimarySource[] = [
  { provider: "google", label: "Google", hint: "Gmail + Calendar", Logo: GoogleLogo },
  { provider: "github", label: "GitHub", hint: "repos", Logo: GitHubLogo },
  { provider: "notion", label: "Notion", hint: "docs", Logo: NotionLogo },
];

/**
 * First-run card shown when no source is connected. Guides the user to connect
 * their first source via inline OAuth buttons (the same getAuthUrl mechanism the
 * integrations page uses). Replaced by BriefingGatheringCard once a source
 * connects. See resolveFirstRunState in src/lib/first-run-state.ts.
 */
export function OnboardingCard() {
  const { addToast } = useToast();
  const [connecting, setConnecting] = useState<string | null>(null);

  async function handleConnect(source: PrimarySource) {
    setConnecting(source.provider);
    try {
      const { url } = await getAuthUrl(source.provider);
      window.location.assign(url);
    } catch {
      addToast(`Couldn't start connecting ${source.label}. Please try again.`, "error");
      setConnecting(null);
    }
  }

  return (
    <div className="rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-8 sm:p-10">
      <div className="flex flex-col items-center text-center max-w-md mx-auto">
        <p className="text-[15px] text-t-primary font-medium mb-1">
          Connect your first source
        </p>
        <p className="text-sm text-t-tertiary leading-relaxed mb-6">
          Muldro gets sharper the more it can see. Connect a source to begin —
          you can add more anytime.
        </p>
        <div className="flex flex-wrap justify-center gap-3 mb-6">
          {PRIMARY_SOURCES.map((source) => {
            const { provider, label, hint, Logo } = source;
            return (
            <button
              key={provider}
              type="button"
              onClick={() => handleConnect(source)}
              disabled={connecting !== null}
              className="flex flex-col items-center gap-1 px-5 py-3 rounded-[var(--radius-md)] border border-b-secondary hover:bg-surface-2 disabled:opacity-50 transition-colors"
            >
              <span aria-hidden="true">
                <Logo className="w-6 h-6" />
              </span>
              <span className="text-[13px] font-medium text-t-primary">
                {connecting === provider ? "Connecting…" : label}
              </span>
              <span className="text-[11px] text-t-tertiary">{hint}</span>
            </button>
            );
          })}
        </div>
        <Link
          href="/integrations"
          className="text-[13px] text-t-secondary hover:text-t-primary underline underline-offset-2"
        >
          See all integrations →
        </Link>
      </div>
    </div>
  );
}

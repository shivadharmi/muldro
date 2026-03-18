"use client";

import type { Briefing } from "@/lib/types";
import { MarkdownRenderer } from "@/components/jarvis/markdown-renderer";
import { BriefingSection } from "./briefing-section";

function itemText(item: Record<string, unknown>): string {
  if (typeof item.title === "string") return item.title;
  if (typeof item.name === "string") return item.name;
  return JSON.stringify(item);
}

export function BriefingViewer({ briefing }: { briefing: Briefing }) {
  return (
    <div className="space-y-4">
      {briefing.headline && (
        <BriefingSection title="Headline">
          <p className="text-sm">{briefing.headline}</p>
        </BriefingSection>
      )}

      {briefing.top_priorities.length > 0 && (
        <BriefingSection title="Top Priorities">
          <ul className="space-y-2">
            {briefing.top_priorities.map((item, i) => (
              <li key={i} className="text-sm text-neutral-300 flex items-start gap-2">
                <span className="text-neutral-600 flex-shrink-0">{i + 1}.</span>
                <span>{itemText(item)}</span>
              </li>
            ))}
          </ul>
        </BriefingSection>
      )}

      {briefing.changes_since_last.length > 0 && (
        <BriefingSection title="Changes Since Last Briefing">
          <ul className="space-y-2">
            {briefing.changes_since_last.map((item, i) => (
              <li key={i} className="text-sm text-neutral-300">
                {itemText(item)}
              </li>
            ))}
          </ul>
        </BriefingSection>
      )}

      {briefing.pending_approvals.length > 0 && (
        <BriefingSection title="Pending Approvals">
          <ul className="space-y-2">
            {briefing.pending_approvals.map((item, i) => (
              <li key={i} className="text-sm text-neutral-300">
                {itemText(item)}
              </li>
            ))}
          </ul>
        </BriefingSection>
      )}

      {briefing.recommended_actions.length > 0 && (
        <BriefingSection title="Recommended Actions">
          <ul className="space-y-2">
            {briefing.recommended_actions.map((action, i) => (
              <li key={i} className="text-sm text-neutral-300 flex items-start gap-2">
                <span className="text-blue-400 flex-shrink-0">&rarr;</span>
                <span>{action}</span>
              </li>
            ))}
          </ul>
        </BriefingSection>
      )}

      {briefing.full_text && (
        <BriefingSection title="Full Text">
          <div className="text-sm text-neutral-400">
            <MarkdownRenderer content={briefing.full_text} />
          </div>
        </BriefingSection>
      )}
    </div>
  );
}

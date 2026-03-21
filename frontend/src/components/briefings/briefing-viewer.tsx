"use client";

import Link from "next/link";
import type { Briefing } from "@/lib/types";
import { MarkdownRenderer, InlineMarkdown } from "@/components/jarvis/markdown-renderer";
import { BriefingSection } from "./briefing-section";

function itemText(item: Record<string, unknown>): string {
  if (typeof item.title === "string") return item.title;
  if (typeof item.name === "string") return item.name;
  if (typeof item.summary === "string") return item.summary;
  if (typeof item.description === "string") return item.description;
  // Handle {entity, change} pattern
  if (typeof item.entity === "string" && typeof item.change === "string") {
    return `${item.entity}: ${item.change}`;
  }
  return JSON.stringify(item);
}

function itemDetail(item: Record<string, unknown>): string | null {
  if (typeof item.description === "string" && typeof item.title === "string") {
    return item.description;
  }
  if (typeof item.summary === "string" && typeof item.title === "string") {
    return item.summary;
  }
  return null;
}

function priorityBadge(priority: unknown) {
  if (typeof priority !== "string") return null;
  const colors: Record<string, string> = {
    critical: "bg-j-error-soft text-j-error",
    high: "bg-j-warning-soft text-j-warning",
    medium: "bg-j-primary-soft text-j-primary",
    low: "bg-surface-3 text-t-tertiary",
  };
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${colors[priority] ?? colors.medium}`}>
      {priority}
    </span>
  );
}

function riskBadge(risk: unknown) {
  if (typeof risk !== "string") return null;
  const colors: Record<string, string> = {
    high: "bg-j-error-soft text-j-error",
    medium: "bg-j-warning-soft text-j-warning",
    low: "bg-j-success-soft text-j-success",
  };
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${colors[risk] ?? ""}`}>
      {risk} risk
    </span>
  );
}

export function BriefingViewer({ briefing }: { briefing: Briefing }) {
  return (
    <div className="space-y-5">
      {briefing.headline && (
        <div className="rounded-[var(--radius-lg)] p-4 bg-j-primary-soft border border-j-primary/20">
          <p className="text-sm font-medium text-j-primary">{briefing.headline}</p>
        </div>
      )}

      {briefing.top_priorities.length > 0 && (
        <BriefingSection title="Top Priorities" variant="priority">
          <div className="space-y-2.5">
            {briefing.top_priorities.map((item, i) => (
              <div key={i} className="flex items-start gap-3 p-2.5 rounded-[var(--radius-sm)] bg-surface-2/50">
                <span className="text-t-muted flex-shrink-0 text-sm font-semibold">{i + 1}.</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-t-primary">{itemText(item)}</span>
                    {priorityBadge(item.priority)}
                  </div>
                  {itemDetail(item) && (
                    <div className="text-xs text-t-tertiary mt-0.5">
                      <InlineMarkdown content={itemDetail(item)!} />
                    </div>
                  )}
                </div>
                <Link
                  href="/chat"
                  className="text-[10px] text-j-primary hover:underline shrink-0"
                >
                  Act on this
                </Link>
              </div>
            ))}
          </div>
        </BriefingSection>
      )}

      {briefing.changes_since_last.length > 0 && (
        <BriefingSection title="Changes Since Last Briefing" variant="info">
          <div className="space-y-2">
            {briefing.changes_since_last.map((item, i) => (
              <div key={i} className="flex items-start gap-2.5 text-sm">
                <span className="text-t-muted shrink-0">&#x2022;</span>
                <div className="min-w-0">
                  <span className="text-t-primary">{itemText(item)}</span>
                  {typeof item.entity === "string" && (
                    <span className="text-[10px] text-t-muted ml-2">
                      via {item.entity as string}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </BriefingSection>
      )}

      {briefing.pending_approvals.length > 0 && (
        <BriefingSection title="Pending Approvals" variant="action">
          <div className="space-y-2">
            {briefing.pending_approvals.map((item, i) => (
              <div key={i} className="flex items-center justify-between p-2 rounded-[var(--radius-sm)] bg-surface-2/50">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-t-primary">{itemText(item)}</span>
                  {riskBadge(item.risk)}
                </div>
                <Link
                  href="/approvals"
                  className="text-[10px] text-j-primary hover:underline"
                >
                  Review
                </Link>
              </div>
            ))}
          </div>
        </BriefingSection>
      )}

      {briefing.recommended_actions.length > 0 && (
        <BriefingSection title="Recommended Actions" variant="action">
          <div className="space-y-2">
            {briefing.recommended_actions.map((action, i) => (
              <div key={i} className="flex items-start gap-2.5 justify-between">
                <div className="flex items-start gap-2 text-sm text-t-primary">
                  <span className="text-j-primary flex-shrink-0">&rarr;</span>
                  <span>{action}</span>
                </div>
                <Link
                  href="/chat"
                  className="text-[10px] text-j-primary hover:underline shrink-0"
                >
                  Do this
                </Link>
              </div>
            ))}
          </div>
        </BriefingSection>
      )}

      {briefing.full_text && (
        <BriefingSection title="Full Briefing" variant="info">
          <div className="text-sm text-t-secondary leading-relaxed">
            <MarkdownRenderer content={briefing.full_text} />
          </div>
        </BriefingSection>
      )}
    </div>
  );
}

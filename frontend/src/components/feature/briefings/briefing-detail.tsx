"use client";

import { MarkdownRenderer } from "@/components/jarvis/markdown-renderer";
import { EvidencePanel } from "@/components/primitives/evidence-panel";
import type { EvidenceBundle } from "@/lib/types/context";

interface RelatedItem {
  item_type: string;
  item_id: string;
  title: string;
  status: string;
}

interface BriefingAction {
  action: string;
  label: string;
}

interface Props {
  headline: string | null;
  fullText: string | null;
  date: string | null;
  confidence: number | null;
  evidence: EvidenceBundle | undefined;
  relatedItems: RelatedItem[];
  actions: BriefingAction[];
  onAction?: (action: string) => void;
}

export function BriefingDetail({
  headline,
  fullText,
  date,
  confidence,
  evidence,
  relatedItems,
  actions,
  onAction,
}: Props) {
  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-t-primary">
          {headline || "Untitled"}
        </h2>
        <div className="flex items-center gap-3 mt-1 text-xs text-t-tertiary">
          {date && <span>{date}</span>}
          {confidence != null && (
            <span>{Math.round(confidence * 100)}% confidence</span>
          )}
        </div>
      </div>

      {/* Actions */}
      {actions.length > 0 && (
        <div className="flex gap-2">
          {actions.map((a) => (
            <button
              key={a.action}
              onClick={() => onAction?.(a.action)}
              className="px-3 py-1 text-xs rounded-[var(--radius-sm)] border border-b-primary text-t-secondary hover:bg-surface-1 transition-colors cursor-pointer"
            >
              {a.label}
            </button>
          ))}
        </div>
      )}

      {/* Full Text */}
      {fullText && (
        <div className="text-sm text-t-secondary leading-relaxed">
          <MarkdownRenderer content={fullText} />
        </div>
      )}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />

      {/* Related Items */}
      {relatedItems.length > 0 && (
        <section>
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            Related
          </h4>
          <ul className="space-y-1">
            {relatedItems.map((item) => (
              <li
                key={item.item_id}
                className="flex items-center gap-2 text-sm text-t-secondary"
              >
                <span className="text-xs text-t-tertiary capitalize">
                  {item.item_type}
                </span>
                <span className="truncate">{item.title}</span>
                <span className="ml-auto text-xs text-t-tertiary capitalize">
                  {item.status}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

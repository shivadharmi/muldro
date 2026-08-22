"use client";

import type { ReactNode } from "react";
import { MarkdownRenderer } from "@/components/muldro/markdown-renderer";
import { SourceIcon } from "@/components/integrations/source-icon";
import { StatusBadge } from "@/components/ui/status-badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { KIND_LABELS, FRAME_STATUS_LABELS, kindStyle, frameStatusColor } from "@/lib/design-tokens";
import type { Unit } from "@/lib/types/unit";

interface Props {
  unit: Unit | null;
  open: boolean;
  onClose: () => void;
  onAct?: (capability: string) => void;
  /** Extra content below layer 4 — the prepared-work queue uses this. */
  children?: ReactNode;
}

/**
 * The Full, as far as spec step 3b builds it.
 *
 * Four layers are specified (spec §5.1). Two are here — layer 2 (the whole
 * body, block markdown) and layer 4 (the affordances) — plus the quotes band
 * standing in for layer 1. Layer 1 by archetype and layer 3, the derivation,
 * are spec step 5; this component is the shell they land in, not a stopgap
 * that gets thrown away.
 *
 * It exists because deleting SurfaceDetailModal with nothing behind the
 * chevron re-creates defect 6 from spec §1 — chevrons that open to nothing.
 *
 * It CANNOT render empty (§2.3). The frame header is unconditional, and the
 * body's absence is stated rather than left blank. Tabs are gone: _TABS_BY_KIND
 * mapped `summary` to Steps/Plan/Events/Trace because nobody could say what a
 * summary was, and four layers replace nine tab lists.
 */
export function UnitDetail({ unit, open, onClose, onAct, children }: Props) {
  if (!open || !unit) return null;
  const { frame, body, quotes } = unit;
  const kindTone = kindStyle(frame.kind);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={frame.headline}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
    >
      <div className="w-full max-w-2xl rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-5 flex flex-col gap-4">
        {/* frame — the same identity the card carried */}
        <div className="flex items-center gap-2">
          <span
            className={`text-[10px] font-medium px-2 py-0.5 rounded-[var(--radius-sm)] ${kindTone.bg} ${kindTone.text}`}
          >
            {KIND_LABELS[frame.kind] ?? frame.kind}
          </span>
          <StatusBadge
            status={frame.status}
            label={FRAME_STATUS_LABELS[frame.status] ?? frame.status}
            dotClass={frameStatusColor(frame.status)}
          />
          <div className="flex-1" />
          <TimeAgo date={frame.updated_at} tone="text-t-muted" className="text-[10px]" />
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-t-muted text-sm px-2 leading-none"
          >
            ×
          </button>
        </div>

        {/* headline — PLAIN TEXT. Never a markdown renderer. */}
        <h2 className="text-base font-medium text-t-primary leading-snug">{frame.headline}</h2>

        <p
          data-testid="unit-detail-context"
          className="text-[11px] text-t-muted font-mono flex items-center gap-1.5"
        >
          <SourceIcon source={frame.source} className="w-3 h-3" />
          <span>{frame.source}</span>
          {frame.entity_type && (
            <>
              <span aria-hidden="true">·</span>
              <span>{frame.entity_type}</span>
            </>
          )}
          <span aria-hidden="true">·</span>
          <span>
            {frame.event_count} {frame.event_count === 1 ? "message" : "messages"}
          </span>
        </p>

        {/* layer 1 (partial) — the thing itself, verbatim and attributed.
            Rendered as PLAIN TEXT: external text is carried verbatim and its
            safety is that no renderer ever treats it as markup. */}
        {quotes.length > 0 && (
          <section data-testid="unit-detail-quotes" className="flex flex-col gap-2">
            <h3 className="text-[11px] font-medium text-t-muted uppercase tracking-wide">
              What arrived
            </h3>
            {quotes.map((q, i) => (
              <div
                key={`${q.who}-${q.when}-${i}`}
                className="border-l-2 border-j-warning bg-j-warning-soft rounded-r-[var(--radius-sm)] px-3 py-2 flex flex-col gap-1"
              >
                <p className="text-xs italic text-t-secondary leading-relaxed whitespace-pre-wrap">
                  {q.text}
                </p>
                <p className="text-[10px] font-mono text-j-warning">
                  — {q.who} · <TimeAgo date={q.when} tone="text-j-warning" />
                </p>
              </div>
            ))}
          </section>
        )}

        {/* layer 2 — muldro's reasoning, the WHOLE body */}
        <section className="flex flex-col gap-2">
          <h3 className="text-[11px] font-medium text-t-muted uppercase tracking-wide">
            What muldro makes of it
          </h3>
          {body ? (
            <div className="text-sm text-t-secondary">
              <MarkdownRenderer content={body} />
            </div>
          ) : (
            // Honest absence, not a blank pane. Deleted when spec step 2b — the
            // body generator — lands and every Unit arrives with prose.
            <p data-testid="unit-detail-no-body" className="text-xs text-t-muted italic">
              Muldro has not written this up yet.
            </p>
          )}
        </section>

        {children}

        {/* layer 4 — act here. This is why the Full is not a link to the source. */}
        {frame.affordances.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap pt-1">
            {frame.affordances.map((a) => (
              <button
                key={a.capability + a.label}
                type="button"
                onClick={() => onAct?.(a.capability)}
                className={
                  a.variant === "primary"
                    ? "text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg font-medium"
                    : "text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-surface-2 text-t-secondary"
                }
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

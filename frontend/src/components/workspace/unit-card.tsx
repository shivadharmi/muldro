"use client";

import type { KeyboardEvent, MouseEvent } from "react";
import type { Unit } from "@/lib/types/unit";
import { Lede } from "./lede";
import { SourceIcon } from "@/components/integrations/source-icon";
import { StatusBadge } from "@/components/ui/status-badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { KIND_LABELS, FRAME_STATUS_LABELS, kindStyle, frameStatusColor } from "@/lib/design-tokens";

interface Props {
  unit: Unit;
  onOpen: () => void;
  onAct?: (capability: string) => void;
  onDismiss?: () => void;
}

const MAX_AFFORDANCES = 3;

/**
 * The Glance. Six slots, always in this order, none of them optional except
 * the quote. Two cards of one kind are the same shape by construction — which
 * is what ranking needs, since you cannot rank things that do not look alike.
 *
 * The headline is PLAIN TEXT and must never reach a markdown renderer: it
 * derives from external strings (an email subject), so a markdown renderer
 * there would let an inbound subject put a live link in muldro's voice.
 *
 * Nothing here truncates with an ellipsis. Truncation is CSS line-clamp only:
 * a complete sentence plus an open affordance says "there is more"; a "…"
 * says "this was cut".
 */
export function UnitCard({ unit, onOpen, onAct, onDismiss }: Props) {
  const { frame, body, quotes } = unit;
  const lede = ledeOf(body);
  const quote = quotes[0];
  const kindTone = kindStyle(frame.kind);
  const affordances = frame.affordances.slice(0, MAX_AFFORDANCES);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen();
    }
  };

  // An affordance acts; it does not also open. The stop lives on the button
  // rather than on a wrapper so the wrapper stays a non-interactive element.
  const act = (e: MouseEvent<HTMLButtonElement>, run: () => void) => {
    e.stopPropagation();
    run();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      className="w-full text-left rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 p-4 flex flex-col gap-2.5 cursor-pointer"
    >
      {/* 1 — header */}
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
      </div>

      {/* 2 — headline (plain text; never a markdown renderer) */}
      <h3 className="text-[13px] font-medium text-t-primary line-clamp-2 leading-snug">
        {frame.headline}
      </h3>

      {/* 3 — context */}
      <p
        data-testid="unit-context"
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

      {/* 4 — lede */}
      <Lede text={lede} />

      {/* 5 — quote (only when there is one) */}
      {quote && (
        <div
          data-testid="unit-quote"
          className="border-l-2 border-j-warning bg-j-warning-soft rounded-r-[var(--radius-sm)] px-2.5 py-1.5 flex flex-col gap-1"
        >
          <p className="text-xs italic text-t-secondary line-clamp-2 leading-relaxed">
            {quote.text}
          </p>
          <p className="text-[10px] font-mono text-j-warning">— {quote.who}</p>
        </div>
      )}

      {/* 6 — affordances */}
      {(affordances.length > 0 || onDismiss) && (
        <div className="flex items-center gap-2 flex-wrap pt-0.5">
          {affordances.map((a) => (
            <button
              key={a.capability + a.label}
              type="button"
              onClick={(e) => act(e, () => onAct?.(a.capability))}
              className={
                a.variant === "primary"
                  ? "text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg font-medium"
                  : "text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-surface-2 text-t-secondary"
              }
            >
              {a.label}
            </button>
          ))}
          {onDismiss && (
            <button
              type="button"
              onClick={(e) => act(e, onDismiss)}
              className="text-xs text-t-muted ml-auto"
            >
              Dismiss
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** A blank line may itself carry whitespace — mirrors backend `_PARAGRAPH_BREAK`. */
const PARAGRAPH_BREAK = /\n\s*\n/;

/**
 * Paragraph one of the body, soft-wraps joined.
 *
 * Mirrors `backend/src/view/body.py::lede_of` exactly — line endings
 * normalized first, a whitespace-bearing blank line still a paragraph break,
 * leading ATX headings skipped. If the two drift, the card's lede and the
 * lede the backend budgeted become two projections of one string that
 * disagree, which is the defect this layer exists to remove.
 */
function ledeOf(body: string): string {
  const normalized = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  for (const block of normalized.split(PARAGRAPH_BREAK)) {
    const lines = block
      .trim()
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"));
    if (lines.length > 0) return lines.join(" ");
  }
  return "";
}

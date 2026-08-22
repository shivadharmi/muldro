"use client";

import { useState } from "react";
import Link from "next/link";
import { ErrorBoundary } from "@/components/error-boundary";
import { UnitCard } from "@/components/workspace/unit-card";
import type { Unit } from "@/lib/types/unit";

interface Props {
  units: Unit[];
  onOpen: (key: string) => void;
  onAct?: (key: string, capability: string) => void;
  onDismiss?: (key: string) => void;
  /**
   * Index into `units` where attention stops. Everything from here on is
   * collapsed behind one row — folded, never filtered: the tail is on the wire
   * and one click away, because a hidden thing that cannot be reached is a lie
   * about coverage. Defaults to showing everything, so a caller that does not
   * know about the fold behaves exactly as before.
   */
  foldAfter?: number;
}

export function WorkspaceCanvas({ units, onOpen, onAct, onDismiss, foldAfter }: Props) {
  // Guarded rather than trusted: this is an index from the wire, and a bad one
  // must cost the fold, never the feed.
  const cut =
    typeof foldAfter === "number" && foldAfter >= 0 && foldAfter <= units.length
      ? foldAfter
      : units.length;
  const [showQuiet, setShowQuiet] = useState(false);
  if (units.length === 0) {
    return (
      <div className="rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-8 sm:p-12">
        <div className="flex flex-col items-center text-center max-w-md mx-auto">
          <div className="w-14 h-14 rounded-full bg-j-success-soft flex items-center justify-center mb-4">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              className="text-j-success"
            >
              <path
                d="M9 12l2 2 4-4"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          </div>
          <p className="text-[15px] text-t-primary font-medium mb-1">
            Nothing needs your attention
          </p>
          <p className="text-sm text-t-tertiary leading-relaxed mb-6">
            Muldro is watching your connected sources. Updates, insights, and action items will appear here.
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
              Connect Sources
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const grid = (items: Unit[]) => (
    <div
      data-testid="unit-grid"
      className="grid gap-3 items-start"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}
    >
      {items.map((u) => (
        <ErrorBoundary
          key={u.frame.key}
          fallback={
            <div className="rounded-[var(--radius-lg)] border border-j-error/20 bg-j-error-soft p-4">
              <p className="text-sm text-t-secondary">Card failed to load</p>
            </div>
          }
        >
          <UnitCard
            unit={u}
            onOpen={() => onOpen(u.frame.key)}
            onAct={(cap) => onAct?.(u.frame.key, cap)}
            onDismiss={onDismiss ? () => onDismiss(u.frame.key) : undefined}
          />
        </ErrorBoundary>
      ))}
    </div>
  );

  const quiet = units.slice(cut);

  return (
    <div className="space-y-3">
      {grid(units.slice(0, cut))}

      {quiet.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowQuiet((v) => !v)}
            aria-expanded={showQuiet}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-[var(--radius-lg)] border border-dashed border-b-secondary text-[13px] text-t-tertiary hover:text-t-secondary hover:bg-surface-2 transition-colors cursor-pointer"
          >
            <span>
              {showQuiet ? "Hide" : "Show"} {quiet.length} quieter{" "}
              {quiet.length === 1 ? "item" : "items"}
            </span>
            <svg
              width="12"
              height="12"
              viewBox="0 0 16 16"
              fill="none"
              className={`transition-transform ${showQuiet ? "rotate-180" : ""}`}
            >
              <path
                d="M4 6l4 4 4-4"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          {showQuiet && <div className="mt-3">{grid(quiet)}</div>}
        </div>
      )}
    </div>
  );
}

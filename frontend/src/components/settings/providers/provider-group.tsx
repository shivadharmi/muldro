"use client";

import type { ReactNode } from "react";

/** §9.2 section header. */
const SEC_H = "text-[11px] font-medium uppercase text-t-muted tracking-[.08em]";

/** §9.3 neutral chip. Re-stated rather than imported: `Chip` is private to
 *  `provider-row.tsx`, and a count beside a group header is not a row slot —
 *  two siblings cross-importing a presentational primitive is a worse
 *  dependency than this duplication. `controls.ts` is its eventual home. */
const COUNT_CHIP =
  "inline-flex items-center h-[20px] px-[8px] rounded-full text-[11px] " +
  "font-medium whitespace-nowrap shrink-0 bg-surface-3 text-t-tertiary " +
  "tabular-nums";

export interface ProviderGroupProps {
  /** Distinguishes the two count chips for tests. */
  id: string;
  title: string;
  count: number;
  className?: string;
  /** The rows, already interleaved with their separators by the owning tab —
   *  which is what keeps a rule off the top of the first row and the bottom of
   *  the last without a row having to know its own index. */
  children: ReactNode;
}

/**
 * One titled, counted card of provider rows (§9.8 `grp`).
 *
 * The header is a real `<h3>`: "Connected" is also a row's status chip text, so
 * a plain span would leave the group label indistinguishable from a row's state
 * to anything navigating by structure — including a test.
 */
export function ProviderGroup({
  id,
  title,
  count,
  className = "",
  children,
}: ProviderGroupProps) {
  return (
    <section aria-label={title} className={className}>
      <div className="flex items-center gap-2 px-[2px] pb-[8px]">
        <h3 className={SEC_H}>{title}</h3>
        <span className={COUNT_CHIP} data-testid={`provider-count-${id}`}>
          {count}
        </span>
      </div>
      <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-lg)] overflow-hidden">
        {children}
      </div>
    </section>
  );
}

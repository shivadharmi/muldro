"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ModelBinding } from "@/lib/types";

/**
 * Put focus back on a provider row after whatever held it has unmounted.
 *
 * Two distinct moments lose focus on the Providers tab, and both drop it on
 * `<body>` inside a focus-trapped modal — from which the next Tab restarts at
 * the top of the trap (WCAG 2.4.3): this confirmation unmounting, and the row
 * itself moving from the Connected card to the Available one once a delete's
 * refetch lands.
 *
 * The target is held in a ref and read by an effect keyed on a tick, so a
 * restore never schedules a render whose only purpose is to clear a flag.
 */
export function useRowFocusRestore(): (provider: string) => void {
  const targetRef = useRef<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (tick === 0) return;
    const provider = targetRef.current;
    targetRef.current = null;
    const active = document.activeElement;
    // Only when focus was ACTUALLY lost. A delete resolves asynchronously, and
    // by then the founder may have moved on — restoring over them would be the
    // same defect pointed the other way.
    if (!provider || (active && active !== document.body && active.isConnected)) {
      return;
    }
    document
      .querySelector<HTMLElement>(`[data-provider-row="${provider}"]`)
      ?.focus();
  }, [tick]);

  return useCallback((provider: string) => {
    targetRef.current = provider;
    setTick((prev) => prev + 1);
  }, []);
}

/**
 * §9.3 `md` ghost button — 32px, padding-x 12px, and 44px below `sm` so it stays
 * a legal touch target. `sm` (30px/11px) is the size assigned to controls inside
 * a dense list row; this panel is card-level, and §9.6 specifies `md` for the
 * warning card's own action.
 *
 * Carries NO text colour: the two variants below set their own, and a colour in
 * the base would compete at equal specificity with the one appended after it —
 * which of the two won would then be decided by Tailwind's output order rather
 * than by this file.
 */
const GHOST_MD =
  "inline-flex items-center justify-center h-[44px] sm:h-[32px] px-[12px] " +
  "text-[13px] font-medium rounded-[var(--radius-md)] bg-transparent " +
  "border border-b-primary hover:bg-surface-2 cursor-pointer " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring";

/** The removal awaiting an answer. Held by the tab, one at a time. */
export interface PendingRemoval {
  provider: string;
  name: string;
  consequence: string;
}

/** "Removing OpenAI breaks the fast tier and the planner override." */
export function consequenceOf(
  name: string,
  bindings: readonly ModelBinding[],
): string {
  const parts = bindings.map(
    (b) => `the ${b.scope_key} ${b.scope_type === "tier" ? "tier" : "override"}`,
  );
  const listed =
    parts.length <= 1
      ? (parts[0] ?? "")
      : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `Removing ${name} breaks ${listed}.`;
}

export interface RemoveConfirmationProps {
  pending: PendingRemoval;
  /** Dismiss without deleting. */
  onCancel: () => void;
  /** Delete anyway. */
  onConfirm: () => void;
}

/**
 * The "this will break something" answer-before-you-delete panel, rendered
 * inline directly beneath the row that raised it.
 *
 * Placement is not cosmetic. At the top of the scroll region this panel was
 * invisible to a founder scrolled to the fifteenth provider, it shifted every
 * row down under the cursor immediately after a destructive click, and — because
 * it preceded every group in DOM order — a keyboard user who had just activated
 * `Remove` tabbed FORWARD into the next provider's buttons and never reached it.
 * Beneath its own row, the reading order and the tab order are the same order.
 *
 * `alertdialog`, not `alert`: `alert` is a passive live region that announces
 * text and never moves focus, which is precisely the failure above. An
 * alertdialog owns focus, so this component takes it on mount, answers Escape,
 * and the tab returns it to the row on dismiss.
 *
 * It is a confirmation, never a block. A credential the founder cannot revoke is
 * a security problem — the dependent bindings buy a sentence and a second click,
 * not a veto.
 */
export function RemoveConfirmation({
  pending,
  onCancel,
  onConfirm,
}: RemoveConfirmationProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const labelId = `remove-confirm-${pending.provider}`;

  // Focus lands on the NON-destructive choice. Opened by a click on `Remove`,
  // this panel's first Enter must not be the delete.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Escape is the universal cancel gesture, and the settings shell listens for
  // it on `document` to close the WHOLE modal. `preventDefault` is the
  // convention that shell documents for a nested overlay closing itself.
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCancel();
    },
    [onCancel],
  );

  return (
    <div
      role="alertdialog"
      aria-labelledby={labelId}
      onKeyDown={handleKeyDown}
      className="flex flex-wrap items-center gap-3 border-l-2 border-j-warning bg-j-warning-soft px-[20px] py-[13px]"
    >
      <p
        id={labelId}
        className="flex-1 min-w-[200px] text-[12.5px] leading-[1.5] text-t-secondary"
      >
        {pending.consequence} Those bindings fail until you point them at a
        connected provider.
      </p>
      <div className="flex items-center gap-[7px] shrink-0">
        <button
          ref={cancelRef}
          type="button"
          onClick={onCancel}
          className={`${GHOST_MD} text-t-secondary`}
        >
          Keep it
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className={`${GHOST_MD} text-j-error`}
        >
          Remove anyway
        </button>
      </div>
    </div>
  );
}

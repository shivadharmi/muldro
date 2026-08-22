"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import type { ModelBinding } from "@/lib/types";
import { btn } from "../controls";

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
 *
 * `scopeRef` is the owning tab's container: the lookup is scoped to it rather
 * than run against `document`, because the settings modal and the standalone
 * settings page can each mount a Providers tab, and a document-wide query would
 * restore focus into whichever copy the DOM happened to yield first.
 */
export function useRowFocusRestore(
  scopeRef: RefObject<HTMLElement | null>,
): (provider: string) => void {
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
    // `CSS.escape`: a provider slug is server data, and an uncatalogued one is
    // whatever was stored — a `"` or `\` in it would make this selector throw
    // a SyntaxError from inside an effect, which React escalates.
    scopeRef.current
      ?.querySelector<HTMLElement>(
        `[data-provider-row="${CSS.escape(provider)}"]`,
      )
      ?.focus();
  }, [scopeRef, tick]);

  return useCallback((provider: string) => {
    targetRef.current = provider;
    setTick((prev) => prev + 1);
  }, []);
}

/**
 * §9.3 `md` ghost — 32px at `sm`+, 44px below it so it stays a legal touch
 * target (§9.10). `sm` (30px) is the size assigned to controls inside a dense
 * list row; this panel is card-level, and §9.6 specifies `md` for the warning
 * card's own action.
 *
 * The tint is a VARIANT rather than a class appended to a shared base: at equal
 * specificity `text-j-error` after `text-t-secondary` is decided by Tailwind's
 * output order, not by the call site. `controls.ts` owns that rule now — this
 * file used to restate both the geometry and the trap.
 *
 * The focus ring goes with the hand-rolled copy and is not replaced: the global
 * `:focus-visible { outline: 2px solid }` in `globals.css` already covers every
 * button on the surface, and the local `focus-visible:outline-none` here was
 * suppressing it in order to redraw the same ring as a box-shadow.
 */
const KEEP_BTN = btn({ size: "md" });
const REMOVE_BTN = btn({ size: "md", variant: "danger" });

/** The removal awaiting an answer. Held by the tab, one at a time. */
export interface PendingRemoval {
  provider: string;
  name: string;
  /** The whole question, built by {@link removalPrompt}. */
  prompt: string;
}

/** "Removing OpenAI breaks the fast tier and the planner override." */
function consequenceOf(name: string, bindings: readonly ModelBinding[]): string {
  const parts = bindings.map(
    (b) => `the ${b.scope_key} ${b.scope_type === "tier" ? "tier" : "override"}`,
  );
  const listed =
    parts.length <= 1
      ? (parts[0] ?? "")
      : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `Removing ${name} breaks ${listed}.`;
}

/**
 * What this removal costs, in the founder's terms.
 *
 * Two texts, because two different things are true. With dependent bindings the
 * cost is downstream and needs naming. With none, the cost is still real — a
 * stored key is destroyed and a workspace credential cannot be read back — so
 * the question is asked anyway, in one plain sentence rather than a warning
 * about consequences that do not exist.
 */
export function removalPrompt(
  name: string,
  bindings: readonly ModelBinding[],
): string {
  if (bindings.length === 0) {
    return `Remove the ${name} key? You'll need to paste it again to reconnect.`;
  }
  return `${consequenceOf(name, bindings)} Those bindings fail until you point them at a connected provider.`;
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
 * and the tab returns it to the row on dismiss. It is the least-bad of the two:
 * the role does promise modality this panel deliberately does not have — it
 * takes focus and answers Escape, but does not trap Tab — so an AT that enters
 * dialog-reading mode will walk out of it into the next row. Trapping Tab inside
 * a panel that is one item in a list would be the worse trade.
 *
 * It asks on EVERY removal, not only a breaking one. A key with no dependent
 * binding is still destroyed by that click and cannot be read back, so the
 * question is the same question; only the sentence differs (see
 * {@link removalPrompt}). Asking is not blocking: `Remove anyway` is one click
 * away in both cases, because a credential the founder cannot revoke is a
 * security problem.
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
        {pending.prompt}
      </p>
      <div className="flex items-center gap-[7px] shrink-0">
        <button
          ref={cancelRef}
          type="button"
          onClick={onCancel}
          className={KEEP_BTN}
        >
          Keep it
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className={REMOVE_BTN}
        >
          Remove anyway
        </button>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef } from "react";

/**
 * Everything the browser will land on with Tab. Deliberately a query rather
 * than a dependency: a modal needs a trap, not a library.
 */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * `display:none` / `visibility:hidden` subtrees are not tabbable, and the
 * settings sheet hides a whole pane per breakpoint — so the rail's buttons must
 * not be in the mobile cycle. Computed style is used rather than
 * `getClientRects()` because jsdom reports no rects for anything at all, which
 * would empty the cycle under test.
 */
function isVisible(el: HTMLElement): boolean {
  const style = window.getComputedStyle(el);
  return style.display !== "none" && style.visibility !== "hidden";
}

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(isVisible);
}

/**
 * Traps Tab inside the returned container while `active`, moves focus into it
 * on activation, and restores focus to whatever was focused beforehand when it
 * deactivates or unmounts (defect A1).
 *
 * The container itself is focused first rather than its first control, so
 * opening the dialog never looks like the user is about to press something.
 */
export function useFocusTrap<T extends HTMLElement>(active: boolean) {
  const containerRef = useRef<T | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // Capture + restore. The cleanup is the restore, so an unmount (the modal
  // returns null when closed) is handled by the same path as a deactivation.
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement;
    restoreRef.current =
      previous instanceof HTMLElement && previous !== document.body
        ? previous
        : null;
    containerRef.current?.focus();
    return () => {
      restoreRef.current?.focus();
      restoreRef.current = null;
    };
  }, [active]);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key !== "Tab") return;
    const container = containerRef.current;
    if (!container) return;

    const items = focusableWithin(container);
    if (items.length === 0) {
      // Nothing to cycle through: keep focus where it is rather than letting
      // Tab escape to the page behind the dialog.
      event.preventDefault();
      return;
    }

    const first = items[0];
    const last = items[items.length - 1];
    const current = document.activeElement;
    const index = current instanceof HTMLElement ? items.indexOf(current) : -1;

    if (event.shiftKey && index <= 0) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (index === -1 || index === items.length - 1)) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [active, onKeyDown]);

  return containerRef;
}

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
 * Tabbable means the element AND every ancestor is rendered — the settings
 * sheet hides a whole pane per breakpoint (`hidden sm:flex`), so the rail's
 * seven buttons must leave the cycle on a phone.
 *
 * The ancestor walk is the whole point: `display` does NOT inherit, so
 * `getComputedStyle(child).display` on a child of a `display:none` element
 * still reports the child's own `flex`. Only `visibility` inherits, which is
 * exactly why checking the element alone LOOKS correct and is not.
 *
 * `el.checkVisibility()` is the one-line browser answer; jsdom does not
 * implement it. Computed style is used rather than `getClientRects()` for the
 * same reason — jsdom reports no rects for anything, which would empty the
 * cycle under test.
 */
function isVisible(el: HTMLElement): boolean {
  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    const style = window.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }
  return true;
}

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(isVisible);
}

export interface FocusTrapOptions {
  /**
   * Suspend the Tab trap without releasing focus. Set while a NESTED overlay
   * (portalled out of this container, e.g. the model picker) owns the keyboard:
   * the trap is document-scoped and pulls focus back in from anywhere, so an
   * unpaused trap would rip focus out of that overlay on its first Tab.
   *
   * Pausing deliberately does NOT restore focus — that rides on unmount, so a
   * nested overlay opening cannot fling focus back to whatever opened the
   * dialog in the first place.
   */
  paused?: boolean;
}

/**
 * Traps Tab inside the returned container, moves focus into it on mount, and
 * restores focus to whatever was focused beforehand on unmount (defect A1).
 *
 * Capture/restore ride on MOUNT, not on a flag: the settings dialog is mounted
 * only while it is open, so its lifetime already is the trap's lifetime, and
 * tying restore to a flag would make every transient pause a focus jump.
 *
 * The container itself is focused first rather than its first control, so
 * opening the dialog never looks like the user is about to press something.
 */
export function useFocusTrap<T extends HTMLElement>({
  paused = false,
}: FocusTrapOptions = {}) {
  const containerRef = useRef<T | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
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
  }, []);

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
    if (paused) return;
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [paused, onKeyDown]);

  return containerRef;
}

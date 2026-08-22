import { useCallback, useEffect, useRef, type RefObject } from "react";

import { inertBackground } from "./inert-background";

/**
 * Everything the browser will land on with Tab. Deliberately a query rather
 * than a dependency: a modal needs a trap, not a library.
 *
 * `[tabindex="-1"]` is excluded from EVERY clause, not just the catch-all.
 * `a[href]` and `button` are focusable by default, so an element that opted out
 * of the tab order — a roving-tabindex row, a restore anchor — still matched
 * and sat in the cycle as a phantom stop the browser itself would skip.
 */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]",
]
  .map((clause) => `${clause}:not([tabindex="-1"])`)
  .join(",");

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
 *
 * The cache is per-sweep and matters: candidates are siblings, so the naive
 * form re-walks and re-measures the same chain once per control.
 */
function visibilityChecker(): (el: Element) => boolean {
  const known = new Map<Element, boolean>();
  return (el: Element): boolean => {
    const chain: Element[] = [];
    let node: Element | null = el;
    let visible = true;
    while (node) {
      const cached = known.get(node);
      if (cached !== undefined) {
        visible = cached;
        break;
      }
      chain.push(node);
      const style = window.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") {
        visible = false;
        break;
      }
      node = node.parentElement;
    }
    // Every node walked shares the verdict: hidden stops at the offending
    // ancestor, so nothing above it is recorded.
    for (const walked of chain) known.set(walked, visible);
    return visible;
  };
}

function focusableWithin(container: HTMLElement): HTMLElement[] {
  const isVisible = visibilityChecker();
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(isVisible);
}

export interface FocusTrapOptions {
  /**
   * Suspend the Tab trap without releasing focus. Set while a NESTED overlay
   * rendered over this container (e.g. the model picker) owns the keyboard:
   * the trap is document-scoped and pulls focus back in from anywhere, so an
   * unpaused trap would rip focus out of that overlay on its first Tab.
   *
   * While paused, Tab is entirely UNCONSTRAINED — this releases the keyboard,
   * it does not hand it over. An overlay that claims `paused` MUST ship its own
   * trap, or Tab walks straight out of both it and the dialog behind it.
   *
   * Pausing deliberately does NOT restore focus: capture and restore ride on
   * mount/unmount, so a nested overlay opening cannot fling focus back to
   * whatever opened the dialog in the first place.
   */
  paused?: boolean;
  /**
   * The subtree to keep reachable; everything outside it is made `inert` and
   * `aria-hidden` for the lifetime of the trap. Usually the whole dialog
   * INCLUDING its backdrop, where the trap container is only the panel — the
   * backdrop must stay clickable. Omit to isolate around the container itself,
   * or pass nothing to skip isolation.
   */
  isolate?: RefObject<HTMLElement | null>;
}

/**
 * Traps Tab inside the returned container, moves focus into it on mount,
 * isolates the rest of the page, and restores focus to whatever was focused
 * beforehand on unmount (defect A1).
 *
 * ONE effect owns capture → isolate → focus, and its cleanup owns release →
 * restore, because both orderings are load-bearing and neither survives being
 * split across two hooks:
 *
 *  - Capture must precede isolation. Per HTML, a node becoming `inert`
 *    unfocuses a focused descendant — and the control that opened the dialog is
 *    in the region about to go inert. Read `document.activeElement` afterwards
 *    and it may already be `body`, so nothing is restored on close and A1
 *    quietly returns.
 *  - Release must precede restore, for the mirror reason: focus cannot be moved
 *    into a still-inert ancestor, and the call fails silently.
 *
 * As two hooks these depended on React iterating effects in declaration order,
 * one direction was always wrong, and jsdom — which implements neither `inert`
 * nor the blur it triggers — could not see either.
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
  isolate,
}: FocusTrapOptions = {}) {
  const containerRef = useRef<T | null>(null);

  useEffect(() => {
    // 1. Capture, BEFORE anything goes inert and takes the focus with it.
    const previous = document.activeElement;
    const restore =
      previous instanceof HTMLElement && previous !== document.body
        ? previous
        : null;

    // 2. Isolate the rest of the page.
    const release = inertBackground(isolate ? isolate.current : containerRef.current);

    // 3. Move focus in.
    containerRef.current?.focus();

    return () => {
      // Release first: the element being restored to lives in that region.
      release();
      restore?.focus();
    };
    // Mount-only, deliberately. `isolate` is documented as a stable ref and
    // nothing can enforce that, so it is NOT a dependency: an inline
    // `isolate={{current: el}}` would re-run this every render, and re-running
    // it is not idempotent — the cleanup fires `restore.focus()`, yanking focus
    // out of the dialog and back to whatever opened it, on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

    // Focused, inside the container, but not IN the cycle — a `tabIndex={-1}`
    // anchor the app focused programmatically. `FOCUSABLE_SELECTOR` excludes
    // those by design (they are not tab stops), so `indexOf` says -1 and the
    // wrap branches below would read that as "at the end" and throw the user to
    // the first control in the whole panel — or, on Shift+Tab, the last.
    //
    // That is precisely the failure the restore was written to prevent: close a
    // dialog, land back on the row you opened it from, press Tab, and get flung
    // to the top of the dialog instead of that row's next control (WCAG 2.4.3).
    // Resolve by DOCUMENT ORDER instead, which is what the browser would have
    // done had the element been a tab stop. Descendants count as following and
    // ancestors as preceding, matching real tab sequence.
    if (index === -1 && current instanceof HTMLElement && container.contains(current)) {
      event.preventDefault();
      const following = items.find(
        (el) =>
          current.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
      );
      const preceding = [...items]
        .reverse()
        .find(
          (el) =>
            current.compareDocumentPosition(el) &
            Node.DOCUMENT_POSITION_PRECEDING,
        );
      // Falling back to the far end is the correct wrap at either extreme.
      (event.shiftKey ? (preceding ?? last) : (following ?? first)).focus();
      return;
    }

    // `index === -1` still reaches here when focus is OUTSIDE the container.
    // That one is a genuine escape, and pulling it back to an end is right.
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

import { useEffect, type RefObject } from "react";

/**
 * A live region is an ANNOUNCEMENT channel, not background content. Toasts are
 * rendered as a sibling of the whole app tree, and the settings surface itself
 * raises them ("Policy mode updated") — inerting them would silence exactly the
 * messages the open dialog produces, and swallow their dismiss button.
 */
function isLiveRegion(el: Element): boolean {
  return (
    el.hasAttribute("aria-live") ||
    el.getAttribute("role") === "status" ||
    el.getAttribute("role") === "alert"
  );
}

/**
 * Hides everything outside `ref` from assistive technology and the pointer
 * while it is mounted.
 *
 * `aria-modal="true"` constrains FOCUS, but a screen reader's virtual cursor
 * still walks the page behind the dialog. The fix walks from the dialog up to
 * `document.body` marking each ancestor's SIBLINGS inert — which is every node
 * outside the dialog's own subtree, and needs no portal to express.
 *
 * An element that already carries `inert` or `aria-hidden` is left alone in
 * both directions, so this can never clear a state it did not set.
 */
export function useInertBackground(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;

    const touched: HTMLElement[] = [];
    let node: HTMLElement | null = root;
    while (node && node !== document.body) {
      const parent: HTMLElement | null = node.parentElement;
      if (!parent) break;
      const siblings: Element[] = Array.from(parent.children);
      for (const sibling of siblings) {
        if (sibling === node || !(sibling instanceof HTMLElement)) continue;
        if (sibling.hasAttribute("inert")) continue;
        if (sibling.hasAttribute("aria-hidden")) continue;
        if (isLiveRegion(sibling)) continue;
        sibling.setAttribute("inert", "");
        sibling.setAttribute("aria-hidden", "true");
        touched.push(sibling);
      }
      node = parent;
    }

    return () => {
      for (const el of touched) {
        el.removeAttribute("inert");
        el.removeAttribute("aria-hidden");
      }
    };
  }, [ref]);
}

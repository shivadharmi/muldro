/**
 * Elements that render nothing. Marking them costs a DOM write and leaves
 * `aria-hidden` litter on Next.js's injected script tags for no benefit.
 */
const NON_RENDERED = new Set(["SCRIPT", "STYLE", "LINK", "TEMPLATE", "NOSCRIPT"]);

/**
 * A live region is an ANNOUNCEMENT channel, not background content. Toasts are
 * rendered as a sibling of the whole app tree, and the settings surface itself
 * raises them ("Policy mode updated") — inerting them would silence exactly the
 * messages the open dialog produces, and swallow their dismiss button.
 */
function isLiveRegion(el: Element): boolean {
  const role = el.getAttribute("role");
  return el.hasAttribute("aria-live") || role === "status" || role === "alert";
}

/**
 * Hides everything outside `root` from assistive technology and the pointer,
 * and returns the release.
 *
 * `aria-modal="true"` constrains FOCUS, but a screen reader's virtual cursor
 * still walks the page behind a dialog. The fix walks from `root` up to
 * `document.body` marking each ancestor's SIBLINGS inert — which is every node
 * outside `root`'s own subtree, and needs no portal to express.
 *
 * `inert` is an HTML-only attribute, so an SVG or MathML sibling (a body-level
 * sprite sheet, say) gets `aria-hidden` and nothing else. That still takes it
 * out of the virtual cursor, which is the part that matters here.
 *
 * An element that already carries `inert` or `aria-hidden` is left alone in
 * both directions, so the release can never clear a state it did not set.
 *
 * KNOWN BOUNDARY: this is a snapshot, not a subscription. Nodes appended to the
 * body AFTER the call — a portal opened later — are not covered. Today that
 * happens to fall the right way for toasts (`ToastContainer` renders `null`
 * when empty, so one raised while the dialog is open is a fresh node and
 * escapes on its own; the live-region exemption only covers toasts already on
 * screen), but any other late portal stays AT-visible. A `MutationObserver`
 * would close it if that ever matters.
 *
 * Not a hook on purpose: the caller must be able to order it against focus
 * capture and restore inside ONE effect. Sequencing it as a second hook made
 * both invariants depend on React's effect ordering — and the capture half
 * silently lost, because a node becoming inert blurs its focused descendant.
 */
export function inertBackground(root: Element | null): () => void {
  if (!root) return () => {};

  const touched: Element[] = [];
  let node: Element | null = root;
  while (node && node !== document.body) {
    const parent: HTMLElement | null = node.parentElement;
    if (!parent) break;
    const siblings: Element[] = Array.from(parent.children);
    for (const sibling of siblings) {
      if (sibling === node) continue;
      if (NON_RENDERED.has(sibling.tagName)) continue;
      if (sibling.hasAttribute("inert")) continue;
      if (sibling.hasAttribute("aria-hidden")) continue;
      if (isLiveRegion(sibling)) continue;
      sibling.setAttribute("aria-hidden", "true");
      if (sibling instanceof HTMLElement) sibling.setAttribute("inert", "");
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
}

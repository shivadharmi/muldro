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
 * An element already hidden by someone else is left alone in both directions,
 * so the release can never clear a state it did not set. "Already hidden" is a
 * question about the VALUE, not the attribute: `aria-hidden="false"` is content
 * someone deliberately kept exposed, and skipping it on the strength of
 * `hasAttribute` alone left it fully readable behind the modal.
 *
 * KNOWN BOUNDARY: this is a snapshot, not a subscription. Nodes appended to the
 * body AFTER the call — a portal opened later — are not covered. Today that
 * happens to fall the right way for toasts (`ToastContainer` renders `null`
 * when empty, so one raised while the dialog is open is a fresh node and
 * escapes on its own; the live-region exemption only covers toasts already on
 * screen), but any other late portal stays AT-visible. A `MutationObserver`
 * would close it if that ever matters.
 *
 * SECOND BOUNDARY, same shape: two body-level dialogs closed out of order.
 * A isolates and marks sibling X; B isolates, sees X already marked and skips
 * it; A closes first and strips X while B is still open. Nesting in the usual
 * order is safe precisely because of the already-marked check — B never claims
 * what A holds — and the settings dialog is the only body-level modal here.
 * A ref-count keyed on the element is the fix if a second one ever lands.
 *
 * Not a hook on purpose: the caller must be able to order it against focus
 * capture and restore inside ONE effect. Sequencing it as a second hook made
 * both invariants depend on React's effect ordering — and the capture half
 * silently lost, because a node becoming inert blurs its focused descendant.
 */
export function inertBackground(root: Element | null): () => void {
  if (!root) return () => {};

  // The PRIOR value travels with each element: an `aria-hidden="false"` node is
  // hidden while the modal is up and must get its explicit `false` back, not be
  // stripped bare, on release.
  const touched: Array<{ el: Element; ariaHidden: string | null }> = [];
  let node: Element | null = root;
  while (node && node !== document.body) {
    const parent: HTMLElement | null = node.parentElement;
    if (!parent) break;
    const siblings: Element[] = Array.from(parent.children);
    for (const sibling of siblings) {
      if (sibling === node) continue;
      if (NON_RENDERED.has(sibling.tagName)) continue;
      if (sibling.hasAttribute("inert")) continue;
      // `="false"` is not hidden — it is content someone kept deliberately
      // visible, and it still has to be hidden behind a modal.
      if (sibling.getAttribute("aria-hidden") === "true") continue;
      if (isLiveRegion(sibling)) continue;
      touched.push({
        el: sibling,
        ariaHidden: sibling.getAttribute("aria-hidden"),
      });
      sibling.setAttribute("aria-hidden", "true");
      if (sibling instanceof HTMLElement) sibling.setAttribute("inert", "");
    }
    node = parent;
  }

  return () => {
    for (const { el, ariaHidden } of touched) {
      el.removeAttribute("inert");
      if (ariaHidden === null) el.removeAttribute("aria-hidden");
      else el.setAttribute("aria-hidden", ariaHidden);
    }
  };
}

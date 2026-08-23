/**
 * The provider row's focus anchor — the class it wears, the attribute that
 * names it, the selector that finds it again, and the one rule for when moving
 * focus to it is legitimate.
 *
 * One module because the writer and the readers are three different files
 * (`provider-list` renders it; `providers-tab` and `remove-confirmation` look it
 * up), and a `data-` attribute spelled out in each of them is a magic string
 * that only fails at run time, silently, as a focus that goes nowhere.
 */

/**
 * The row wrapper is focusable BY SCRIPT only: it is where focus returns after
 * the removal confirmation unmounts, and where a founder sent from the Model
 * tab lands, so that focus never falls to `<body>` inside a focus-trapped
 * modal.
 *
 * It carries no focus classes of its own. It used to wear
 * `outline-none focus-visible:ring-1 focus-visible:ring-inset ring-j-ring`, and
 * both halves of that were wrong: Tailwind emits its utilities inside
 * `@layer utilities` while `globals.css`'s `:focus-visible` rule is UNLAYERED,
 * and an unlayered normal declaration beats every layered one whatever its
 * specificity. So the `outline-none` never suppressed anything, and the ring
 * was a SECOND indicator in a second colour drawn over the global one. See the
 * note over that rule in `globals.css`.
 */
const ROW_ANCHOR_ATTR = "data-provider-row";

/** Spread onto the row wrapper. A function rather than a literal in the JSX so
 *  the attribute name exists exactly once in the codebase. */
export function rowAnchorAttrs(provider: string): Record<string, string> {
  return { [ROW_ANCHOR_ATTR]: provider };
}

/** Find a row's anchor. `CSS.escape`: a provider slug is server data, and an
 *  uncatalogued one is whatever was stored — a `"` or `\` in it would make this
 *  selector throw a SyntaxError from inside an effect, which React escalates. */
export function rowAnchorSelector(provider: string): string {
  return `[${ROW_ANCHOR_ATTR}="${CSS.escape(provider)}"]`;
}

/**
 * Whether focus is currently nowhere, and may therefore be moved.
 *
 * The whole point of restoring focus is that an unmount dropped it on `<body>`
 * inside a focus trap. If it has since landed somewhere real — the founder typed
 * into the search box while a delete was in flight — then moving it is the same
 * defect pointed the other way, so every caller asks this first.
 */
export function focusWasLost(): boolean {
  const active = document.activeElement;
  return !active || active === document.body || !active.isConnected;
}

/**
 * Shared assertions for the §9.10 mobile pass.
 *
 * **These pin a CLASS CONTRACT, not a rendering.** jsdom has no layout engine
 * and no stylesheet: `getBoundingClientRect()` returns zeros for every element,
 * `getComputedStyle` never resolves a Tailwind utility, and no media query is
 * ever evaluated. A test that asserted "this control is 44px tall" would
 * therefore pass against a control with no height class at all — it would be a
 * guard that cannot fail, which is worse than no guard. So the specs that import
 * this module assert the responsive classes that PRODUCE §9.10's metrics, on the
 * elements that must carry them. **The pixel result is verified in a browser.**
 *
 * The widths below are named for readability and are never applied to anything:
 * nothing here resizes a viewport, because in jsdom that would change nothing.
 * They are the two design widths §9.10 is written against.
 */

import { expect } from "vitest";

/** Tailwind's `sm`. Every §9.10 override is "below this", i.e. the unprefixed
 *  utility, with the desktop value carried on the `sm:` variant. */
export const SM_BREAKPOINT = 640;

/** The phone the mobile column of §9.10 is written for. */
export const MOBILE_WIDTH = 390;

/** The laptop the desktop layout (L1, L2) is written for. */
export const DESKTOP_WIDTH = 1024;

/** §9.10's touch-target floor, and the WCAG 2.5.8 minimum it comes from. */
export const TOUCH_TARGET_PX = 44;

export function classesOf(el: Element): string[] {
  return (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
}

/**
 * A short, stable identity for an element, used ONLY in failure messages.
 *
 * A sweep that fails with "expected false to be true" tells you a control on the
 * surface is too small and nothing else; with this you get the tag, the type and
 * the accessible-ish name of the one that broke.
 */
export function describeControl(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const type = el.getAttribute("type");
  const name =
    el.getAttribute("aria-label") ??
    el.getAttribute("id") ??
    (el.textContent ?? "").trim().slice(0, 40);
  return `<${tag}${type ? ` type="${type}"` : ""}> ${name || "(unnamed)"}`;
}

/** Present unprefixed, so it applies BELOW `sm` (and above, unless overridden). */
export function expectBaseClass(el: Element, cls: string, what: string): void {
  expect(classesOf(el), `${what} — ${describeControl(el)}`).toContain(cls);
}

/** Present as a `sm:` variant, so it applies at `sm` and up. */
export function expectSmClass(el: Element, cls: string, what: string): void {
  expect(classesOf(el), `${what} — ${describeControl(el)}`).toContain(
    `sm:${cls}`,
  );
}

/**
 * This element declares §9.10's 44px floor below `sm`.
 *
 * `h-[44px]` OR `min-h-[44px]`: a fixed height is right for a control whose
 * content is one line (every input, select and button on the surface), and a
 * floor is right where the content may legitimately grow — the settings rail's
 * rows carry a counted suffix and must be allowed to wrap rather than clip.
 * Both satisfy the touch target; neither is satisfied by a `sm:`-prefixed copy,
 * which is exactly the mistake this catches.
 */
export function expectTouchTarget(el: Element, what: string): void {
  const classes = classesOf(el);
  const declared =
    classes.includes(`h-[${TOUCH_TARGET_PX}px]`) ||
    classes.includes(`min-h-[${TOUCH_TARGET_PX}px]`);
  expect(
    declared,
    `${what} must declare a ${TOUCH_TARGET_PX}px touch target below \`sm\` ` +
      `(${MOBILE_WIDTH}px) — ${describeControl(el)} has [${classes.join(" ")}]`,
  ).toBe(true);
}

/** Every focusable control inside a subtree, in DOM order. */
export function controlsIn(root: Element): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("button, input, select"));
}

/** Assert the whole subtree's controls clear the touch-target floor. */
export function expectAllTouchTargets(root: Element, what: string): void {
  const controls = controlsIn(root);
  expect(controls.length, `${what} rendered no controls to check`).toBeGreaterThan(0);
  for (const control of controls) expectTouchTarget(control, what);
}

// ── The binding grid's column contract (L1) ────────────────────────────────

/** The number of tracks a `grid-cols-…` utility declares.
 *
 *  Both forms are read rather than only the arbitrary one: the grid is
 *  `grid-cols-2` below `sm` and `grid-cols-[2.7fr_1fr_1.1fr_1.1fr]` above it,
 *  and a test that understood only one of the two would silently skip the other. */
export function trackCount(utility: string): number {
  const arbitrary = /^grid-cols-\[(.+)\]$/.exec(utility);
  if (arbitrary) return arbitrary[1].split("_").length;
  const numeric = /^grid-cols-(\d+)$/.exec(utility);
  return numeric ? Number(numeric[1]) : 0;
}

/** The grid's track count at one breakpoint. `0` when the breakpoint declares
 *  no `grid-cols-…` at all, which every caller asserts against. */
export function columnsAt(grid: Element, breakpoint: "base" | "sm"): number {
  const prefix = breakpoint === "sm" ? "sm:" : "";
  for (const cls of classesOf(grid)) {
    const bare = breakpoint === "sm" ? cls.slice(prefix.length) : cls;
    const prefixed = breakpoint === "sm" ? cls.startsWith(prefix) : !cls.includes(":");
    if (prefixed && bare.startsWith("grid-cols-")) return trackCount(bare);
  }
  return 0;
}

/** How many tracks one cell occupies at a breakpoint. Unspanned cells are 1;
 *  a `sm:col-span-…` overrides the base one, which is how Model and Temperature
 *  stop spanning once there are four tracks to spread across. */
export function spanOf(cell: Element, breakpoint: "base" | "sm"): number {
  const classes = classesOf(cell);
  const read = (utility: string): number | null => {
    const match = /^col-span-(\d+)$/.exec(utility);
    return match ? Number(match[1]) : null;
  };
  const base = classes.map(read).find((n): n is number => n !== null) ?? 1;
  if (breakpoint === "base") return base;
  const sm = classes
    .filter((c) => c.startsWith("sm:"))
    .map((c) => read(c.slice(3)))
    .find((n): n is number => n !== null);
  return sm ?? base;
}

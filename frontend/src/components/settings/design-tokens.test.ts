/**
 * The settings surface names no design token `globals.css` does not define.
 *
 * `tsc` and `eslint` cannot fail on `text-j-danger`, because a Tailwind class
 * is a string. That one shipped, rendered nothing at all, and survived months
 * of review with the Remove button simply not red. This is the guard for that
 * whole class of defect, applied to every settings component rather than to the
 * one that happened to get it first.
 *
 * The sweep reads SOURCE, not a rendered tree, and that is the point: the
 * dangerous token is the one on the branch nobody renders — a status map entry,
 * a variant table row, the untaken half of a ternary. `design-tokens.ts`
 * explains what the source scan can and cannot see.
 *
 * The self-tests below come FIRST because a guard that cannot fail is worse
 * than no guard: they pin that a misspelled token is reported, and that the
 * skip rules which keep `text-sm` and `border-t` quiet do not also swallow it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, test, expect } from "vitest";

import {
  definedTokens,
  tokenOf,
  tokensIn,
  tokensInSource,
  undefinedTokensInSource,
} from "./design-tokens";

/** Every component named in the accessibility sweep, plus the modules that hold
 *  colour on their behalf — `controls.ts` owns every button and control
 *  variant, `trust-constants.ts` owns the trust dot, and a component's own file
 *  therefore never names those tokens at all. */
const COMPONENTS = [
  "settings-modal.tsx",
  "settings-rail.tsx",
  "controls.ts",
  "icons.tsx",
  "model/tier-card.tsx",
  "model/binding-fields.tsx",
  "model/save-bar.tsx",
  "model/agent-overrides.tsx",
  "model/model-picker.tsx",
  "providers/provider-row.tsx",
  "providers/provider-group.tsx",
  "providers/provider-list.tsx",
  "providers/provider-credential-form.tsx",
  "providers/remove-confirmation.tsx",
  "providers/provider-filter.tsx",
  "providers/row-anchor.ts",
  "tabs/model-tab.tsx",
  "tabs/providers-tab.tsx",
  "tabs/account-tab.tsx",
  "tabs/policy-tab.tsx",
  "tabs/preferences-tab.tsx",
  "tabs/spending-tab.tsx",
  "tabs/trust-tab.tsx",
  "tabs/trust-capability-card.tsx",
  "tabs/trust-constants.ts",
] as const;

/** The files that carry no colour of their own — every class they render comes
 *  from `controls.ts` or from a prop. Listed rather than inferred, so a file
 *  that STOPS naming colours has to be moved here deliberately instead of
 *  quietly ceasing to be checked. */
const COLOURLESS: ReadonlySet<string> = new Set([
  "icons.tsx",
  "providers/provider-list.tsx",
  "providers/row-anchor.ts",
]);

const read = (name: string) =>
  readFileSync(join(process.cwd(), "src/components/settings", name), "utf8");

describe("the resolver", () => {
  test("reads the token set out of globals.css rather than a copy of it", () => {
    const tokens = definedTokens();
    expect(tokens.has("j-error")).toBe(true);
    expect(tokens.has("surface-2")).toBe(true);
    expect(tokens.has("j-primary-hover")).toBe(true);
    // The typo that shipped, and the one `provider-row.tsx` names as the trap.
    expect(tokens.has("j-danger")).toBe(false);
    expect(tokens.has("j-eror")).toBe(false);
  });

  test("a misspelled token is reported, through every variant and tint", () => {
    for (const dead of [
      "text-j-danger",
      "bg-j-eror",
      "bg-surface-9/50",
      "hover:bg-j-primary-hoverr",
      "sm:focus-visible:ring-j-rng",
      "border-l-j-primry",
    ]) {
      expect(undefinedTokensInSource(dead)).toHaveLength(1);
    }
  });

  test("the skip rules stay narrow enough to leave the guard biting", () => {
    // Named a token, and it resolves.
    for (const live of [
      "bg-surface-2/50",
      "hover:bg-j-primary-hover",
      "sm:text-t-muted",
      "border-l-j-primary",
      "border-t-muted",
      "focus-visible:ring-j-ring",
      "text-white",
      "bg-black/50",
      "to-surface-0",
      "bg-transparent",
    ]) {
      expect(tokenOf(live)).not.toBeNull();
      expect(undefinedTokensInSource(live)).toEqual([]);
    }
    // Names no token at all — a size, a side, a style, an arbitrary value.
    for (const notAToken of [
      "text-sm",
      "text-[11.5px]",
      "text-center",
      "border-t",
      "border-l-2",
      "border-dashed",
      "border-[1.5px]",
      "bg-gradient-to-b",
      "outline-none",
      "ring-2",
      "ring-inset",
      "ring-offset-2",
      "shadow-[0_0_0_1px_var(--muldro-primary-soft)]",
    ]) {
      expect(tokenOf(notAToken)).toBeNull();
    }
  });

  test("the rendered half walks the subtree, root included", () => {
    // The DOM, not React: `tokensIn` takes an `Element`, and every settings
    // spec that uses it (`model/save-bar.test.tsx` today) hands it one out of
    // a render. Built by hand here so this file stays the resolver's own
    // self-test rather than a second copy of a component's fixtures.
    const root = document.createElement("section");
    root.className = "bg-surface-2 sm:bg-surface-2/50 border-t border-b-secondary";
    const child = document.createElement("span");
    child.className = "text-j-primary hover:text-j-primary-hover text-[11.5px]";
    root.append(child);

    const { tokens, undefinedTokens } = tokensIn(root);
    expect(undefinedTokens).toEqual([]);
    expect(tokens.sort()).toEqual([
      "b-secondary",
      "j-primary",
      "j-primary-hover",
      "surface-2",
    ]);

    child.className = "text-j-danger";
    expect(tokensIn(root).undefinedTokens).toEqual(["j-danger"]);
  });

  test("a class quoted in prose is prose, not markup", () => {
    const prose = "/** a typo (`bg-j-eror`) renders an INVISIBLE dot */";
    expect(undefinedTokensInSource(prose)).toEqual([]);
    expect(undefinedTokensInSource("// text-j-danger was never a token")).toEqual([]);
  });
});

describe("every settings component", () => {
  test.each(COMPONENTS)("%s names only defined tokens", (name) => {
    const { tokens, undefinedTokens } = tokensInSource(read(name));
    expect(undefinedTokens).toEqual([]);
    // A guard that found nothing to check is not a guard.
    if (!COLOURLESS.has(name)) expect(tokens.length).toBeGreaterThan(0);
  });
});

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
 * a variant table row, the untaken half of a ternary. `token-audit.ts`
 * explains what the source scan can and cannot see.
 *
 * The self-tests below come FIRST because a guard that cannot fail is worse
 * than no guard: they pin that a misspelled token is reported, and that the
 * skip rules which keep `text-sm` and `border-t` quiet do not also swallow it.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, test, expect } from "vitest";

import {
  definedTokens,
  tokenOf,
  tokensIn,
  tokensInSource,
  undefinedTokensInSource,
} from "./token-audit";

/**
 * Every non-test module under `src/`, WALKED rather than listed.
 *
 * **Walked, because a hand-maintained list is a guard with a hole in it that
 * nothing reports.** A component added next week would simply not be swept, the
 * suite would stay green, and the defect would ship — which is the failure mode
 * this whole module exists to close.
 *
 * **The whole app, not just `settings/`, because the defect was never confined
 * to `settings/`.** Widening the sweep the first time found five live instances
 * outside it: `bg-status-success`, `bg-status-error` and `bg-status-warning` on
 * the activity feed's status dots (there is no `--color-status-*` at all, so
 * those dots painted nothing), and `accent-primary` in four more files, where
 * `globals.css` defines `accent-50/100/400/500/600` and no `accent-primary`.
 * That is `text-j-danger` verbatim, five more times, none of it in this tree.
 *
 * The cost of the wider net is false positives, and it was measured rather than
 * assumed: across 175 files the only thing it flagged was the five real defects
 * and this module's own prefix table. `stroke-width` and friends are SVG
 * presentation attributes sharing a prefix with a colour utility, and they are
 * enumerated in `NON_COLOUR` for exactly that reason.
 *
 * Fixtures are excluded with the tests: they are data for a spec, not a surface
 * anyone sees, and their class strings are assertions rather than markup. This
 * module is excluded too — its `COLOUR_PREFIXES` array is a vocabulary of class
 * PREFIXES held as data, so the sweep reads `"ring-offset"` as a `ring-` utility
 * naming a token called `offset`. It is the auditor, not a surface.
 */
const SRC_DIR = join(fileURLToPath(import.meta.url), "../../..");

function walk(dir: string, prefix = ""): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const name = `${prefix}${entry.name}`;
    if (entry.name === "node_modules") continue;
    if (entry.isDirectory()) {
      found.push(...walk(join(dir, entry.name), `${name}/`));
    } else if (
      /\.tsx?$/.test(entry.name) &&
      !entry.name.includes(".test.") &&
      !entry.name.includes("-fixtures.") &&
      entry.name !== "token-audit.ts"
    ) {
      found.push(name);
    }
  }
  return found.sort();
}

const COMPONENTS = walk(SRC_DIR);

/**
 * The files that MUST resolve at least one token.
 *
 * The inverse of the allowlist this started as, and the inversion is the point.
 * An allowlist of colourless files has to name every hook, context and value
 * module on the surface, grows with each one added, and states nothing anybody
 * cares about. This list states the thing that matters: these are the modules
 * that paint, and a sweep that stopped finding tokens in one of them has
 * stopped looking rather than been satisfied.
 *
 * `controls.ts` and `tabs/trust-constants.ts` are here because they hold colour
 * on other components' behalf — every button variant and the trust dot — which
 * is exactly why several components that clearly render markup are NOT here.
 */
const COLOUR_BEARING = [
  "components/settings/settings-modal.tsx",
  "components/settings/settings-rail.tsx",
  "components/settings/controls.ts",
  "components/settings/model/tier-card.tsx",
  "components/settings/model/binding-fields.tsx",
  "components/settings/model/save-bar.tsx",
  "components/settings/model/agent-overrides.tsx",
  "components/settings/model/model-picker.tsx",
  "components/settings/providers/provider-row.tsx",
  "components/settings/providers/provider-group.tsx",
  "components/settings/providers/provider-credential-form.tsx",
  "components/settings/providers/remove-confirmation.tsx",
  "components/settings/providers/provider-filter.tsx",
  "components/settings/tabs/model-tab.tsx",
  "components/settings/tabs/providers-tab.tsx",
  "components/settings/tabs/account-tab.tsx",
  "components/settings/tabs/policy-tab.tsx",
  "components/settings/tabs/preferences-tab.tsx",
  "components/settings/tabs/spending-tab.tsx",
  "components/settings/tabs/trust-tab.tsx",
  "components/settings/tabs/trust-capability-card.tsx",
  "components/settings/tabs/trust-constants.ts",
] as const;

const read = (name: string) => readFileSync(join(SRC_DIR, name), "utf8");

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
      // The arbitrary-opacity spelling. Tailwind takes both `/50` and `/[0.5]`,
      // and testing for `[` before stripping the modifier skipped the second
      // entirely — a one-keystroke bypass on a surface full of tints.
      "bg-j-danger/[0.5]",
      "hover:bg-surface-9/[.35]",
      // `ring-offset-` takes a colour. Classifying `offset` as geometry to keep
      // `ring-offset-2` quiet silenced this with it.
      "ring-offset-j-danger",
      "focus-visible:ring-offset-surface-9",
      "inset-ring-j-danger",
    ]) {
      expect(undefinedTokensInSource(dead), dead).toHaveLength(1);
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
      "focus-visible:ring-offset-surface-0",
      "bg-j-primary/[0.5]",
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
      // SVG presentation attributes share the `stroke-` prefix with the colour
      // utility; kebab-case ones appear in data-URI SVGs and `transition` strings.
      "stroke-width",
      "stroke-linecap",
      "stroke-dasharray",
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
  test("the walk found the whole tree, not an empty directory", () => {
    // `test.each([])` registers NOTHING and the file still reports green, so the
    // sweep's own reach is asserted before the sweep.
    expect(COMPONENTS.length).toBeGreaterThan(150);
    expect(COMPONENTS).toContain("components/settings/labels.ts");
    expect(COMPONENTS).toContain("components/settings/tabs/model-tab.tsx");
    // Outside the settings tree, where widening the net found five live defects.
    expect(COMPONENTS).toContain("components/primitives/live-activity-feed.tsx");
    expect(COMPONENTS).toContain("lib/design-tokens.ts");
    // The auditor audits everything but itself — see the walk's docblock.
    expect(COMPONENTS).not.toContain("components/settings/token-audit.ts");
    expect(COMPONENTS).toContain("components/settings/providers/provider-row.tsx");
    expect(COMPONENTS.some((f) => f.includes(".test."))).toBe(false);
    expect(COMPONENTS.some((f) => f.includes("-fixtures."))).toBe(false);
  });

  test("every file the walk names is a file the walk can read", () => {
    // A path the walk produced but `read()` cannot open would surface as a
    // thrown ENOENT inside one case; asserting it here names the walk instead.
    for (const name of COLOUR_BEARING) expect(COMPONENTS).toContain(name);
  });

  test.each(COMPONENTS)("%s names only defined tokens", (name) => {
    expect(undefinedTokensInSource(read(name))).toEqual([]);
  });

  test.each(COLOUR_BEARING)("%s still names colour at all", (name) => {
    // A guard that found nothing to check is not a guard.
    expect(tokensInSource(read(name)).tokens.length).toBeGreaterThan(0);
  });
});

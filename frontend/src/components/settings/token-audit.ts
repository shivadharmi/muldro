/**
 * Resolve the design tokens a settings component NAMES against the ones
 * `globals.css` actually DEFINES.
 *
 * Named `token-audit`, not `design-tokens`: `lib/design-tokens.ts` already
 * exists, is production code with ten importers, and hands out Tailwind colour
 * classes from a status map — which is exactly the kind of place a dead token
 * hides. Two modules under one name with opposite bundling constraints (this
 * one imports `node:fs` and must never reach a bundle) is a trap. The
 * `-fixtures` / `-audit` suffix is this surface's convention for test-only
 * modules.
 *
 * This is the one guard in the settings tree that catches a defect class both
 * `tsc` and `eslint` are structurally blind to: **a Tailwind class is a string,
 * and a nonexistent token is just a string that generates no rule.** Three
 * `text-j-danger` uses shipped in `tabs/model-tab.tsx` and survived months of
 * review — `globals.css` defines `--color-j-error`, never `j-danger` — so the
 * Remove button was simply not red, and nothing anywhere said so.
 *
 * The mechanism was written for `model/save-bar.test.tsx` against one rendered
 * subtree; it lives here so every settings component gets it. Two entry points,
 * because they catch different things:
 *
 *   * {@link undefinedTokensIn} reads a RENDERED tree. It proves the classes
 *     that actually reached the DOM on that path resolve.
 *   * {@link undefinedTokensInSource} reads a SOURCE file. It reaches the
 *     branches a render did not take — the status maps in `trust-constants.ts`,
 *     the variant tables in `controls.ts`, the `expanded ? … : …` ternaries —
 *     which is where a dead token hides longest, because nobody renders the
 *     branch that was never right.
 *
 * **What neither can see:** a class assembled at run time from a fragment
 * (`"bg-j-" + tone`). No settings component does that today, and
 * `providers/provider-row.tsx` documents why its dot tone is a union of whole
 * literals rather than a stem plus a suffix.
 *
 * A concatenated stem is invisible to both. A TEMPLATE-literal one is not quite:
 * the source scan reads the stem up to the interpolation and reports `j-` as an
 * undefined token, which is a true finding wearing a confusing name — there is
 * no token called `j-`, and a reader will go looking for one. Treat it as "this
 * class is composed at run time and cannot be checked", and rewrite the call
 * site as whole literals, which is what `provider-row.tsx` did.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Utility prefixes whose value is a colour token. `shadow-` is deliberately
 *  absent: every shadow on this surface is an arbitrary value or a size. */
const COLOUR_PREFIXES = [
  "bg",
  "text",
  "border",
  "outline",
  "divide",
  "fill",
  "stroke",
  "caret",
  "accent",
  "decoration",
  "placeholder",
  // Longest-first: `ring-offset-j-danger` must not be read as `ring-` plus
  // `offset-j-danger`. `PREFIX` alternates in this order, so these two have to
  // precede `ring`, which is why the list is not alphabetical.
  "ring-offset",
  "inset-ring",
  "from",
  "via",
  "to",
  "ring",
] as const;

/** Colours Tailwind resolves with no `--color-*` entry of our own. */
const BUILTIN = ["transparent", "current", "inherit", "white", "black"];

/**
 * Suffixes those same prefixes take that are NOT colours — font sizes, border
 * widths and styles, alignment, background geometry.
 *
 * Deliberately an enumeration rather than a shape rule. Anything not listed is
 * treated as a token and must resolve, which is what keeps a MISSPELLED token
 * (`j-danger`, `j-eror`, `surface-9`, `t-mutted`) inside the guard: none of
 * them can be mistaken for a member of a closed vocabulary.
 */
const NON_COLOUR = new Set([
  // Font-size scale.
  "xs", "sm", "base", "lg", "xl",
  "2xl", "3xl", "4xl", "5xl", "6xl", "7xl", "8xl", "9xl",
  // Alignment, wrapping, truncation.
  "left", "center", "right", "justify", "start", "end",
  "balance", "pretty", "wrap", "nowrap", "ellipsis", "clip",
  // Widths shared by border-/ring-/outline-/divide-.
  "0", "1", "2", "4", "8",
  // Line styles, and the "no line at all" keyword.
  "solid", "dashed", "dotted", "double", "hidden", "none",
  // Sides and axes: `border-b`, `border-x`, `divide-y`.
  "t", "r", "b", "l", "s", "e", "x", "y",
  // Ring geometry.
  "inset",
  // Background geometry.
  "fixed", "local", "scroll", "auto", "cover", "contain",
  "top", "bottom", "repeat", "round", "space",
  // SVG presentation ATTRIBUTES, which share the `stroke-` prefix with the
  // colour utility. `settings/icons.tsx` escapes this only because JSX spells
  // them camelCase; a plain `style`/`transition` string or a data-URI SVG
  // elsewhere in the app spells them kebab-case and would otherwise be read as
  // four undefined colour tokens.
  "width", "linecap", "linejoin", "dasharray", "dashoffset", "miterlimit",
  "opacity",
]);

/** Multi-segment suffixes whose FIRST segment settles that it is not a colour:
 *  `bg-gradient-to-b`, `ring-offset-2`, `bg-repeat-x`, `bg-clip-text`. */
const NON_COLOUR_HEADS = new Set([
  "gradient", "linear", "radial", "conic",
  "origin", "clip", "size", "position", "blend", "repeat", "no",
]);

/** `t-`, `b-`, `l-` … as a SIDE rather than as the first half of a token name.
 *  `border-l-j-primary` is side `l` plus `j-primary`; `border-t-muted` is the
 *  token `t-muted`. The whole remainder is tried first, so the token wins. */
const SIDE = /^[trblsexy]-/;

const PREFIX = new RegExp(`^(?:${COLOUR_PREFIXES.join("|")})-(.+)$`);

const GLOBALS_CSS = join(
  fileURLToPath(import.meta.url),
  "../../../app/globals.css",
);

let defined: Set<string> | null = null;

/**
 * Every `--color-*` name `globals.css` declares, plus Tailwind's built-ins.
 * Parsed, never transcribed — a hand-copied list is the same defect wearing a
 * second hat.
 *
 * Resolved relative to THIS FILE, not to `process.cwd()`. A cwd-relative path
 * works only when vitest is started from `frontend/`; from the repo root with
 * `--root frontend`, or from a workspace runner, `readFileSync` throws ENOENT
 * and every assertion in the sweep fails at once naming a missing FILE. The
 * natural reaction to that is to disable the sweep, which is the one outcome
 * this guard cannot survive.
 */
export function definedTokens(): Set<string> {
  if (defined) return defined;
  const css = readFileSync(GLOBALS_CSS, "utf8");
  const names = Array.from(
    css.matchAll(/--color-([a-z0-9-]+)\s*:/g),
    (m) => m[1],
  );
  defined = new Set([...names, ...BUILTIN]);
  return defined;
}

/**
 * The token a single class names, or `null` when it names no token at all.
 *
 * Variant prefixes (`sm:`, `hover:`, `focus-visible:`, `disabled:`) and opacity
 * suffixes (`/50`, `/35`) are stripped, so a hover colour and a tinted fill are
 * both checked. Arbitrary values (`text-[11.5px]`, or a bracketed background-image
 * URL) name no token and are skipped.
 *
 * NOTE: never spell a bracketed arbitrary class literally in a comment on this
 * surface — write it in prose instead, as above. Tailwind scans
 * source files for class-like strings and does not skip comments, so it emitted
 * `background-image: url(…)` into globals.css and the CSS build failed with
 * "Module not found: Can't resolve '…'". Every test, tsc and eslint stayed green —
 * only starting the app revealed it.
 */
export function tokenOf(raw: string): string | null {
  const utility = raw.split(":").pop() ?? "";
  // Opacity FIRST, then the arbitrary-value test. The other order is a
  // one-keystroke bypass: Tailwind also spells the modifier `/[0.5]`, and an
  // `includes("[")` that runs first skips `bg-j-danger/[0.5]` entirely — on a
  // surface already full of `/50` tints, the likeliest variant anyone writes.
  const base = utility.replace(/\/(?:\d+|\[[^\]]*\])$/, "");
  if (base.includes("[")) return null;
  const match = PREFIX.exec(base);
  if (!match) return null;
  const value = match[1];
  if (NON_COLOUR.has(value)) return null;
  if (NON_COLOUR_HEADS.has(value.split("-")[0])) return null;
  // A side plus a width, not a side plus a colour: `border-l-2`.
  if (SIDE.test(value) && NON_COLOUR.has(value.slice(2))) return null;
  return value;
}

/** Split a token candidate into the names that would satisfy it: the whole
 *  remainder, and — for `border-l-j-primary` — the remainder past the side. */
function candidates(value: string): string[] {
  return SIDE.test(value) ? [value, value.slice(2)] : [value];
}

export interface TokenScan {
  /** Tokens named AND defined. Non-empty is what proves the scan looked. */
  tokens: string[];
  /** Tokens named and NOT defined. This is the defect. */
  undefinedTokens: string[];
}

function partition(classes: Iterable<string>): TokenScan {
  const known = definedTokens();
  const tokens = new Set<string>();
  const missing = new Set<string>();
  for (const raw of classes) {
    const value = tokenOf(raw);
    if (value === null) continue;
    if (candidates(value).some((name) => known.has(name))) tokens.add(value);
    else missing.add(value);
  }
  return { tokens: [...tokens], undefinedTokens: [...missing] };
}

/** Every colour token a rendered subtree names, split by whether it resolves. */
export function tokensIn(root: Element): TokenScan {
  const classes: string[] = [];
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    classes.push(...(el.getAttribute("class") ?? "").split(/\s+/));
  }
  return partition(classes);
}

/** The tokens a rendered subtree names that `globals.css` does not define. */
export function undefinedTokensIn(root: Element): string[] {
  return tokensIn(root).undefinedTokens;
}

/** Prose is not markup. Every doc block on this surface quotes class names —
 *  `provider-row.tsx` names `bg-j-eror` to explain the defect it guards — so a
 *  scan that read comments would report the examples as the disease. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/gm, "$1");
}

/** Every colour token a SOURCE file names, split by whether it resolves —
 *  including the branches a render did not take. */
export function tokensInSource(source: string): TokenScan {
  const words = stripComments(source).match(/[A-Za-z][\w:/[\].%-]*/g) ?? [];
  return partition(words);
}

/** The tokens a source file names that `globals.css` does not define. */
export function undefinedTokensInSource(source: string): string[] {
  return tokensInSource(source).undefinedTokens;
}

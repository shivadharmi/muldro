/**
 * Turning a slug into something a founder can read — the **A3** rule, in one
 * place.
 *
 * A3 was logged against the model `<select>` that announced `google_genai`, and
 * for a while it was fixed only there. But the defect was never "selects
 * announce slugs": it is that `google_genai` means nothing to a founder, and it
 * means exactly as little on a tier card as it did in the select. Fixing the
 * instance left the surface disagreeing with itself — the same uncatalogued
 * provider read "Google genai" in the picker and `google_genai` in the tier card
 * and the provider row, which is worse than either name alone, because two
 * names for one thing invite the reading that they are two things.
 *
 * **This module lives at the settings root, not under `model/`.** The rule was
 * written inside `model/model-picker-catalog.ts`, but `providers/provider-row.tsx`
 * and `providers/provider-list.tsx` need it too, and importing it from there
 * would make a providers component depend on a model-picker module for no
 * reason but where the function happened to be typed first. The root is where
 * this surface already keeps what every folder shares — `controls.ts`,
 * `design-tokens.ts`, `icons.tsx`, `overlay-context.ts`.
 *
 * **A humanised slug is still a fallback, never a lookup.** Every call site
 * reaches here only after the catalog failed to supply a `display_name`. The
 * point is that an unresolvable slug renders as words rather than as an
 * identifier, not that this module knows any provider's real name.
 */

/** Capitalise the first character and leave the rest alone. Deliberately not a
 *  title-caser: `Claude Opus 4.5` and `gpt-4o` must survive being passed
 *  through, and per-word capitalisation would rewrite both. */
export const sentenceCase = (text: string): string =>
  text.charAt(0).toUpperCase() + text.slice(1);

/**
 * `google_genai` → `Google genai`, `anthropic_adaptive` → `Anthropic adaptive`.
 *
 * Underscores to spaces, then sentence case. One word in, one word out: a slug
 * with no underscore is just sentence-cased, which is why tier names
 * (`reasoning`, `fast`) and agent names (`planner`) can go through the same
 * function as a provider slug instead of each site keeping its own copy.
 */
export const humaniseSlug = (slug: string): string =>
  sentenceCase(slug.replace(/_/g, " ").trim());

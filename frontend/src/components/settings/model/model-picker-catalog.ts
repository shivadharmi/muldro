import type { CatalogModel, CatalogProvider, ProviderStatus } from "@/lib/types";
import { humaniseSlug, sentenceCase } from "../labels";

/**
 * The §9.9 palette's data layer: catalog rows in, labelled and grouped rows out.
 *
 * Split from `model-picker.tsx` because none of it is UI — every function here
 * is pure, and the two halves fail differently. A wrong label is an **A3**
 * defect (a slug reached the screen); a wrong group is a §4.7 defect (a model
 * the founder cannot bind was offered anyway). Keeping them in one file also
 * put the palette at the 400-line cap with no headroom.
 */

/** `thinking_style` is a provider-shaped slug; the row needs the *behaviour*,
 *  and the provider has its own column. Unknown styles are humanised (**A3**). */
const THINKING_LABELS: Record<string, string> = {
  anthropic_adaptive: "Adaptive",
  anthropic_legacy: "Budgeted",
  openai_effort: "Effort",
  gemini: "Thinking",
  none: "No thinking",
};

export const thinkingLabel = (style: string): string =>
  THINKING_LABELS[style] ?? humaniseSlug(style);

/** 200000 → `200K`. The unit is what makes the 52px column legible. */
export function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${Math.round((tokens / 1_000_000) * 10) / 10}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

/**
 * Per-1k stored, per-Mtok shown, so ×1000.
 *
 * Rounded because `0.005 * 1000` is not exactly 5 in binary floating point, then
 * fixed to two places: `$5`, `$0.25` and `$2.5` in one right-aligned
 * `tabular-nums` column do not read as a column — the decimal points have to
 * line up for the prices to be comparable at a glance, which is the only reason
 * the column exists.
 */
export const usdPerMtok = (perThousand: number): string =>
  `$${(Math.round(perThousand * 1000 * 100) / 100).toFixed(2)}`;

export const costLabel = (m: CatalogModel): string =>
  `${usdPerMtok(m.input_cost_per_1k)} / ${usdPerMtok(m.output_cost_per_1k)}`;

/** Searchable text. Numeric facts go in twice — formatted *and* raw — so
 *  `"200k"` and `"200000"` both find the same model. */
function haystack(m: CatalogModel, providerName: string): string {
  const parts = [m.display_name, providerName, thinkingLabel(m.thinking_style),
    formatContext(m.context_window), m.context_window, costLabel(m)];
  return parts.join(" ").toLowerCase();
}

/** Every term must match, not the whole query as one string: a concatenated
 *  `includes` fails on `"anthropic sonnet"` — never adjacent in one field. */
const matchesQuery = (hay: string, terms: readonly string[]): boolean =>
  terms.every((term) => hay.includes(term));

/** Split a raw query into the terms every row must satisfy. */
export const queryTerms = (query: string): string[] =>
  query.toLowerCase().split(/\s+/).filter(Boolean);

export const connectedProviders = (
  statuses: readonly ProviderStatus[],
): ReadonlySet<string> =>
  new Set(statuses.filter((s) => s.configured).map((s) => s.provider));

export interface Row {
  /** Unique per RENDERED row — a suggested model repeats in its provider's
   *  group, so a bare `model_id` collides and breaks `aria-activedescendant`. */
  id: string;
  model: CatalogModel;
  providerName: string;
}

export interface Group {
  key: string;
  title: string;
  /** Only "Suggested" crosses providers, so only it earns the provider column. */
  crossProvider: boolean;
  rows: Row[];
}

export function buildGroups(
  uid: string, models: readonly CatalogModel[], providers: readonly CatalogProvider[],
  connected: ReadonlySet<string>, tier: string, terms: readonly string[],
): Group[] {
  // No catalog entry means no display name, and **A3** forbids the slug.
  const nameOf = (slug: string): string =>
    providers.find((p) => p.provider === slug)?.display_name ??
    humaniseSlug(slug);

  // Only connected providers are offered. §2.4 rejects a binding to an
  // unconfigured provider with a 422, so a cross-provider Suggested row from a
  // disconnected one would be a dead control, not merely a warned binding. They
  // are not hidden either: the footer NAMES them and routes to Providers.
  const visible = models.filter(
    (m) =>
      connected.has(m.provider) && matchesQuery(haystack(m, nameOf(m.provider)), terms),
  );
  const rowsOf = (key: string, subset: readonly CatalogModel[]): Row[] =>
    subset.map((model) => ({
      id: `${uid}-${key}-${model.provider}-${model.model_id}`,
      model,
      providerName: nameOf(model.provider),
    }));

  const groups: Group[] = [];
  const suggested = visible.filter((m) => m.suggested_tier === tier);
  if (suggested.length > 0) {
    groups.push({ key: "suggested", title: `Suggested for ${sentenceCase(tier)}`,
      crossProvider: true, rows: rowsOf("suggested", suggested) });
  }
  for (const { provider, display_name } of providers) {
    if (!connected.has(provider)) continue;
    const rows = rowsOf(provider, visible.filter((m) => m.provider === provider));
    if (rows.length === 0) continue;
    groups.push({ key: provider, title: display_name, crossProvider: false, rows });
  }
  return groups;
}

/**
 * The footer's unconnected line, or `null` when everything is connected.
 *
 * NAMES them rather than only counting them: §4.7's purpose is that the missing
 * prerequisite stops being invisible, and "2 providers not connected" still
 * leaves the founder to guess which models they are not being offered. Capped
 * at two names so the line cannot outgrow the footer.
 */
export function unconnectedLabel(
  providers: readonly CatalogProvider[],
  connected: ReadonlySet<string>,
): string | null {
  const names = providers
    .filter((p) => !connected.has(p.provider))
    .map((p) => p.display_name);
  if (names.length === 0) return null;
  const noun = names.length === 1 ? "provider" : "providers";
  const rest = names.length - 2;
  const shown = names.slice(0, 2).join(", ") + (rest > 0 ? ` +${rest} more` : "");
  return `${names.length} ${noun} not connected: ${shown}`;
}

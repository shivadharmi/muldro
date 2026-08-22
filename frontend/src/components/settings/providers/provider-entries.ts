import type {
  CatalogProvider,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";

/**
 * One provider as the Providers tab needs it: server state, plus the catalog
 * entry carrying the display name and credential schema.
 *
 * `entry` is null for an uncatalogued provider — derived from `catalogued` as
 * well as from the lookup, so a stale catalog row cannot resurrect a schema the
 * server has disowned.
 */
export interface ProviderEntry {
  status: ProviderStatus;
  entry: CatalogProvider | null;
  /** Lower-cased provider slug, display name, model names and model ids. */
  haystack: string;
}

/**
 * Join the saved config's provider list to the catalog, and build the string
 * each row is searched against.
 *
 * The haystack carries the MODELS as well as the provider, because a founder
 * names the model — "sonnet", "gpt-5" — and which vendor hosts it is the thing
 * they came here to look up.
 */
export function buildEntries(
  config: ModelConfig | null,
  catalog: ModelCatalog | null,
): ProviderEntry[] {
  const byProvider = new Map(
    (catalog?.providers ?? []).map((p) => [p.provider, p]),
  );
  const models = new Map<string, string>();
  for (const model of catalog?.models ?? []) {
    const prior = models.get(model.provider) ?? "";
    models.set(model.provider, `${prior} ${model.display_name} ${model.model_id}`);
  }
  return (config?.providers ?? []).map((status) => {
    const entry = status.catalogued
      ? (byProvider.get(status.provider) ?? null)
      : null;
    const haystack = `${status.provider} ${entry?.display_name ?? ""} ${
      models.get(status.provider) ?? ""
    }`.toLowerCase();
    return { status, entry, haystack };
  });
}

/**
 * Filter by the search box.
 *
 * Every whitespace-separated TERM must appear, rather than the query as one
 * substring. The haystack is a concatenation of four different fields, so the
 * boundaries between them are not word boundaries a founder can see or predict:
 * a substring match makes `"anthropic sonnet"` — provider then model, the most
 * natural way to narrow a list — return nothing, because the two words are
 * separated by the rest of the display name inside the joined string.
 */
export function filterEntries(
  entries: readonly ProviderEntry[],
  query: string,
): ProviderEntry[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [...entries];
  return entries.filter((item) =>
    terms.every((term) => item.haystack.includes(term)),
  );
}

/**
 * The bindings this provider currently backs — read from the SAVED config.
 *
 * `CredentialDeleteResult.orphaned_bindings` is the authority on what a delete
 * broke, but it only exists once the credential is already gone. A confirmation
 * has to be answerable BEFORE that, so the consequence is computed from the
 * config already on screen. The two can disagree if the server moved underneath
 * us, which is exactly why the post-delete result is still surfaced too.
 */
export function dependentBindings(
  config: ModelConfig | null,
  provider: string,
): ModelBinding[] {
  if (!config) return [];
  return [...config.tiers, ...config.agent_overrides].filter(
    (binding) => binding.provider === provider,
  );
}

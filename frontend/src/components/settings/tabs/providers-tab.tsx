"use client";

import Link from "next/link";
import { Fragment, useCallback, useMemo, useState, type ReactNode } from "react";

import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import type {
  CatalogProvider,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";
import type { CredentialFields } from "../hooks/use-provider-credentials";
import { useModelConfigContext } from "../model-config-context";
import { ProviderCredentialForm } from "../providers/provider-credential-form";
import {
  ProviderFilter,
  type ProviderFilterValue,
} from "../providers/provider-filter";
import { ProviderRow, ProviderRowSeparator } from "../providers/provider-row";

/** §9.2 section header. */
const SEC_H = "text-[11px] font-medium uppercase text-t-muted tracking-[.08em]";

/** §9.3 neutral chip. Re-stated rather than imported: `Chip` is private to
 *  `provider-row.tsx`, and a count beside a group header is not a row slot. */
const COUNT_CHIP =
  "inline-flex items-center h-[20px] px-[8px] rounded-full text-[11px] " +
  "font-medium whitespace-nowrap shrink-0 bg-surface-3 text-t-tertiary " +
  "tabular-nums";

/** §9.3 `ctl`, in its search-field form. */
const SEARCH_CTL =
  "flex-1 min-w-0 flex items-center justify-start gap-[9px] h-[44px] sm:h-[36px] " +
  "px-[12px] sm:px-[10px] rounded-[var(--radius-md)] bg-surface-2 " +
  "border border-b-secondary text-t-muted";

const GHOST_BTN =
  "inline-flex items-center justify-center h-[44px] sm:h-[30px] px-[11px] " +
  "text-[13px] font-medium rounded-[var(--radius-md)] bg-transparent " +
  "border border-b-primary hover:bg-surface-2 cursor-pointer";

function SearchGlyph() {
  return (
    <svg
      viewBox="0 0 12 12"
      width={13}
      height={13}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle cx="5.2" cy="5.2" r="3.3" />
      <path d="M7.7 7.7l2.1 2.1" />
    </svg>
  );
}

/** Server state plus the catalog entry carrying the display name and credential
 *  schema. `entry` is null for an uncatalogued provider — derived from
 *  `catalogued` as well as from the lookup, so a stale catalog row cannot
 *  resurrect a schema the server disowned. */
interface ProviderEntry {
  status: ProviderStatus;
  entry: CatalogProvider | null;
  /** Lower-cased provider slug, display name, model names and model ids. */
  haystack: string;
}

/**
 * Search matches the provider AND its models. A founder types "sonnet", not
 * "anthropic" — the model is the thing they can name, and which vendor hosts it
 * is the detail they came here to look up.
 */
function buildEntries(
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

/** "Removing OpenAI breaks the fast tier and the planner override." */
function consequenceOf(name: string, bindings: readonly ModelBinding[]): string {
  const parts = bindings.map(
    (b) => `the ${b.scope_key} ${b.scope_type === "tier" ? "tier" : "override"}`,
  );
  const listed =
    parts.length <= 1
      ? (parts[0] ?? "")
      : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `Removing ${name} breaks ${listed}.`;
}

interface PendingRemoval {
  provider: string;
  name: string;
  consequence: string;
}

function ProviderGroup({
  id,
  title,
  count,
  className = "",
  children,
}: {
  id: string;
  title: string;
  count: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className={className}>
      <div className="flex items-center gap-2 px-[2px] pb-[8px]">
        <h3 className={SEC_H}>{title}</h3>
        <span className={COUNT_CHIP} data-testid={`provider-count-${id}`}>
          {count}
        </span>
      </div>
      <div className="bg-surface-1 border border-b-secondary rounded-[var(--radius-lg)] overflow-hidden">
        {children}
      </div>
    </section>
  );
}

/**
 * The Providers tab: every model provider this workspace can call, its
 * credential state, and the schema-driven form that changes it.
 *
 * It owns exactly one piece of list state the pieces below it deliberately do
 * not — which row is expanded. Expansion is exclusive, and a row cannot enforce
 * that about its siblings.
 */
export function ProvidersTab() {
  const { addToast } = useToast();
  const { models, credentials } = useModelConfigContext();
  const { catalog, config } = models;

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProviderFilterValue>("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingRemoval | null>(null);

  const entries = useMemo(() => buildEntries(config, catalog), [config, catalog]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((item) => item.haystack.includes(needle));
  }, [entries, query]);

  const connected = visible.filter((item) => item.status.configured);
  const available = visible.filter((item) => !item.status.configured);
  const showConnected = filter !== "available" && connected.length > 0;
  const showAvailable = filter !== "connected" && available.length > 0;

  const remove = useCallback(
    async (provider: string, name: string) => {
      try {
        const result = await credentials.remove(provider);
        if (result.orphaned_bindings.length > 0) {
          const broken = result.orphaned_bindings.map((w) => w.message).join(" ");
          addToast(`${name} removed. ${broken}`, "warning");
        } else {
          addToast(`${name} credentials removed`, "success");
        }
      } catch (err) {
        addToast(errorToMessage(err), "error");
      }
    },
    [addToast, credentials],
  );

  // Confirmation, never a block. A credential the founder cannot revoke is a
  // security problem, so a dependent binding buys a sentence and a second
  // click — not a veto.
  const handleRemove = useCallback(
    (provider: string, name: string) => {
      const bindings = dependentBindings(config, provider);
      if (bindings.length === 0) {
        void remove(provider, name);
        return;
      }
      setPending({ provider, name, consequence: consequenceOf(name, bindings) });
    },
    [config, remove],
  );

  const confirmRemoval = useCallback(() => {
    if (!pending) return;
    const { provider, name } = pending;
    setPending(null);
    void remove(provider, name);
  }, [pending, remove]);

  const handleTest = useCallback(
    async (provider: string, name: string) => {
      try {
        const result = await credentials.test(provider);
        addToast(`${name} test: ${result.status}`, "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      }
    },
    [addToast, credentials],
  );

  const handleSave = useCallback(
    async (provider: string, name: string, fields: CredentialFields) => {
      try {
        await credentials.save(provider, fields);
      } catch (err) {
        addToast(errorToMessage(err), "error");
        // Re-thrown so the form keeps what was typed: a rejected 60-character
        // key must not have to be pasted a second time.
        throw err;
      }
      addToast(`${name} credentials saved`, "success");
      setExpanded(null);
    },
    [addToast, credentials],
  );

  const renderRow = (item: ProviderEntry) => {
    const { status, entry } = item;
    const provider = status.provider;
    const name = entry?.display_name ?? provider;
    const open = expanded === provider;
    const busy = credentials.isBusy(provider);
    return (
      <ProviderRow
        status={status}
        catalog={entry}
        expanded={open}
        busy={busy}
        onToggle={() => setExpanded(open ? null : provider)}
        onTest={() => void handleTest(provider, name)}
        onRemove={() => handleRemove(provider, name)}
      >
        {/* An uncatalogued provider declares no credential schema, so it gets no
            form at all rather than a zero-field one. */}
        {entry && (
          <ProviderCredentialForm
            provider={entry}
            status={status}
            busy={busy}
            onSubmit={(fields) => handleSave(provider, name, fields)}
          />
        )}
      </ProviderRow>
    );
  };

  const rows = (list: readonly ProviderEntry[]) =>
    list.map((item, index) => (
      <Fragment key={item.status.provider}>
        {index > 0 && <ProviderRowSeparator />}
        {renderRow(item)}
      </Fragment>
    ));

  return (
    <div className="flex flex-col">
      {/* §2.2. An inference key is not an install: it grants Muldro no access
          to the founder's world and mints no capability. */}
      <p className="text-[12.5px] leading-[1.5] text-t-tertiary">
        API keys and endpoints Muldro may call for inference. They grant no
        access to your accounts — app connections like Gmail, Slack and GitHub
        live in{" "}
        <Link
          href="/integrations"
          className="text-j-primary hover:underline underline-offset-2"
        >
          Integrations
        </Link>
        .
      </p>

      {/* §9.8 puts the toolbar at `14px 24px 12px`. The 24px is the settings
          panel's own horizontal padding, so only the vertical pair is set here
          — supplying it again would inset the toolbar past the rows below it. */}
      <div className="flex items-center gap-[10px] pt-[14px] pb-[12px]">
        <label className={SEARCH_CTL}>
          <SearchGlyph />
          <span className="sr-only">Search providers</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Search ${entries.length} providers`}
            autoComplete="off"
            spellCheck={false}
            className="flex-1 min-w-0 bg-transparent text-[15px] sm:text-[14px] text-t-primary placeholder:text-t-muted outline-none"
          />
        </label>
        <ProviderFilter value={filter} onChange={setFilter} />
      </div>

      {pending && (
        <div
          role="alert"
          className="mb-[14px] flex flex-wrap items-center gap-3 rounded-[var(--radius-lg)] border border-j-warning/35 bg-j-warning-soft px-4 py-3"
        >
          <p className="flex-1 min-w-[200px] text-[12.5px] leading-[1.5] text-t-secondary">
            {pending.consequence} Those bindings fail until you point them at a
            connected provider.
          </p>
          <div className="flex items-center gap-[7px] shrink-0">
            <button
              type="button"
              onClick={() => setPending(null)}
              className={`${GHOST_BTN} text-t-secondary`}
            >
              Keep it
            </button>
            <button
              type="button"
              onClick={confirmRemoval}
              className={`${GHOST_BTN} text-j-error`}
            >
              Remove anyway
            </button>
          </div>
        </div>
      )}

      {showConnected && (
        <ProviderGroup
          id="connected"
          title="Connected"
          count={connected.length}
          className="mb-[18px]"
        >
          {rows(connected)}
        </ProviderGroup>
      )}

      {showAvailable && (
        <ProviderGroup id="available" title="Available" count={available.length}>
          {rows(available)}
        </ProviderGroup>
      )}

      {!showConnected && !showAvailable && (
        <p className="py-6 text-center text-[12.5px] text-t-muted">
          {entries.length === 0 ? "Loading providers…" : "No providers match."}
        </p>
      )}

      {/* A long list must read as continuing rather than as ending at the fold. */}
      <div
        aria-hidden="true"
        className="sticky bottom-0 h-[56px] -mt-[56px] shrink-0 pointer-events-none bg-gradient-to-b from-transparent to-surface-1"
      />
    </div>
  );
}

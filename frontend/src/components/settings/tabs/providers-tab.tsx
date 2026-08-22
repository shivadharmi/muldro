"use client";

import Link from "next/link";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import { ctl } from "../controls";
import { SearchIcon } from "../icons";
import type { CredentialFields } from "../hooks/use-provider-credentials";
import { useModelConfigContext } from "../model-config-context";
import { ProviderCredentialForm } from "../providers/provider-credential-form";
import {
  buildEntries,
  dependentBindings,
  filterEntries,
  type ProviderEntry,
} from "../providers/provider-entries";
import {
  ProviderFilter,
  type ProviderFilterValue,
} from "../providers/provider-filter";
import { ProviderRow, ProviderRowSeparator } from "../providers/provider-row";
import {
  consequenceOf,
  RemoveConfirmation,
  useRowFocusRestore,
  type PendingRemoval,
} from "../providers/remove-confirmation";

/** §9.2 section header. */
const SEC_H = "text-[11px] font-medium uppercase text-t-muted tracking-[.08em]";

/** §9.3 neutral chip. Re-stated rather than imported: `Chip` is private to
 *  `provider-row.tsx`, and a count beside a group header is not a row slot —
 *  two tab-level siblings cross-importing a presentational primitive is a worse
 *  dependency than this duplication. `controls.ts` is its eventual home. */
const COUNT_CHIP =
  "inline-flex items-center h-[20px] px-[8px] rounded-full text-[11px] " +
  "font-medium whitespace-nowrap shrink-0 bg-surface-3 text-t-tertiary " +
  "tabular-nums";

/** The row wrapper is focusable BY SCRIPT only: it is where focus returns after
 *  the removal confirmation unmounts, so that focus never falls to `<body>`
 *  inside a focus-trapped modal. */
const ROW_ANCHOR =
  "outline-none focus-visible:ring-1 focus-visible:ring-inset " +
  "focus-visible:ring-j-ring";

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
 *
 * Exclusivity DISCARDS a half-typed credential: opening a second row unmounts
 * the first form and the values in it. That is the intended trade. Lifting those
 * values into this component to survive the collapse would hold a plaintext key
 * in memory for the rest of the visit, across every unrelated re-render, for the
 * sake of a form the founder navigated away from — the credential form already
 * clears its own secrets the moment the server holds them, and this is the same
 * rule applied to abandonment.
 */
export function ProvidersTab() {
  const { addToast } = useToast();
  const { models, credentials } = useModelConfigContext();
  const { catalog, config, loading, load } = models;

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProviderFilterValue>("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingRemoval | null>(null);

  // `useModelConfigContext` fires the same `load()` and swallows the failure,
  // because a shared context cannot know which of its consumers is on screen to
  // be told. This tab IS on screen, and both calls share one promise — so this
  // is not a second request, it is the one place that observes the outcome. A
  // failure resets the hook's guard, which is what makes the retry below work.
  useEffect(() => {
    load().catch((err) => addToast(errorToMessage(err), "error"));
  }, [load, addToast]);

  const entries = useMemo(() => buildEntries(config, catalog), [config, catalog]);
  const visible = useMemo(() => filterEntries(entries, query), [entries, query]);
  const connected = useMemo(
    () => visible.filter((item) => item.status.configured),
    [visible],
  );
  const available = useMemo(
    () => visible.filter((item) => !item.status.configured),
    [visible],
  );

  const showConnected = filter !== "available" && connected.length > 0;
  const showAvailable = filter !== "connected" && available.length > 0;

  const restoreFocusTo = useRowFocusRestore();

  const closeConfirmation = useCallback(
    (provider: string) => {
      setPending(null);
      restoreFocusTo(provider);
    },
    [restoreFocusTo],
  );

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
      } finally {
        // The refetch has already been adopted, so this batches with the render
        // that moves the row between groups.
        restoreFocusTo(provider);
      }
    },
    [addToast, credentials, restoreFocusTo],
  );

  const handleRemove = useCallback(
    (provider: string, name: string) => {
      const bindings = dependentBindings(config, provider);
      if (bindings.length === 0) {
        // An open confirmation for a DIFFERENT provider is stale the moment this
        // one deletes: clear it rather than leave it answering for a row whose
        // neighbour just changed underneath it.
        setPending(null);
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
    closeConfirmation(provider);
    void remove(provider, name);
  }, [closeConfirmation, pending, remove]);

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

  // The confirmation is interleaved directly beneath its own row, so reading
  // order, tab order and the thing being answered for are all the same place.
  const rows = (list: readonly ProviderEntry[]) =>
    list.map((item, index) => {
      const provider = item.status.provider;
      return (
        <Fragment key={provider}>
          {index > 0 && <ProviderRowSeparator />}
          <div data-provider-row={provider} tabIndex={-1} className={ROW_ANCHOR}>
            {renderRow(item)}
          </div>
          {pending?.provider === provider && (
            <>
              <ProviderRowSeparator />
              <RemoveConfirmation
                pending={pending}
                onCancel={() => closeConfirmation(provider)}
                onConfirm={confirmRemoval}
              />
            </>
          )}
        </Fragment>
      );
    });

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
          — supplying it again would inset the toolbar past the rows below it,
          and would hard-code 24px over §9.10's 16px mobile gutter. */}
      <div className="flex items-center gap-[10px] pt-[14px] pb-[12px]">
        <label
          className={ctl({
            extra: "flex items-center justify-start gap-[9px] flex-1 min-w-0",
          })}
        >
          <SearchIcon size={13} className="text-t-muted" />
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
        <div className="py-6 text-center text-[12.5px] text-t-muted">
          {loading ? (
            "Loading providers…"
          ) : config === null ? (
            // A failed load is NOT an empty list. Say so, and offer the retry —
            // the hook resets its guard on failure, so `load()` really refetches.
            <>
              <p>Providers could not be loaded.</p>
              <button
                type="button"
                onClick={() =>
                  void load().catch((err) =>
                    addToast(errorToMessage(err), "error"),
                  )
                }
                className="mt-2 text-[12.5px] font-medium text-j-primary hover:underline cursor-pointer"
              >
                Try again
              </button>
            </>
          ) : entries.length === 0 ? (
            "No providers available."
          ) : (
            "No providers match."
          )}
        </div>
      )}

      {/* A long list must read as continuing rather than as ending at the fold.
          §9.8 says `to-surface-1`, which is the panel's fill at `sm`+ — but the
          shell's scroll body is `bg-surface-0` below it, where a surface-1 fade
          would end in a visible band. Deliberately spec-literal only where the
          spec's own assumption holds. */}
      <div
        aria-hidden="true"
        className="sticky bottom-0 h-[56px] -mt-[56px] shrink-0 pointer-events-none bg-gradient-to-b from-transparent to-surface-0 sm:to-surface-1"
      />
    </div>
  );
}

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import { ctl } from "../controls";
import { SearchIcon } from "../icons";
import { useExpandedRow } from "../hooks/use-expanded-row";
import type { CredentialFields } from "../hooks/use-provider-credentials";
import { useModelConfigContext } from "../model-config-context";
import { ProviderGroup } from "../providers/provider-group";
import { ProviderList } from "../providers/provider-list";
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
import {
  removalPrompt,
  useRowFocusRestore,
  type PendingRemoval,
} from "../providers/remove-confirmation";
import { focusWasLost, rowAnchorSelector } from "../providers/row-anchor";

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
 *
 * Every removal is confirmed, breaking or not — see `removalPrompt`. This is the
 * one surface where a stored API key is destroyed, and the panel asks rather
 * than vetoes.
 */
export function ProvidersTab() {
  const { addToast } = useToast();
  const { models, credentials } = useModelConfigContext();
  const { catalog, config, loading, load } = models;

  const containerRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProviderFilterValue>("all");
  // Which row is open, seeded from the Model tab's `Connect {provider}` — see
  // `useExpandedRow`, which owns the one-shot intent and its clearing.
  const {
    expanded,
    arrivedAt,
    open: openRow,
    close: closeRow,
  } = useExpandedRow();
  const [pending, setPending] = useState<PendingRemoval | null>(null);

  // `useModelConfigContext` fires the same `load()` and swallows the failure,
  // because a shared context cannot know which of its consumers is on screen to
  // be told. This tab IS on screen, and both calls share one promise — so this
  // is not a second request, it is the one place that observes the outcome. A
  // failure resets the hook's guard, which is what makes the retry work.
  const retryLoad = useCallback(
    () => load().catch((err) => addToast(errorToMessage(err), "error")),
    [load, addToast],
  );
  // Whether the fetch has finished — succeeded or failed. Not the same fact as
  // `loading`, which is false BOTH before the load starts and after it ends;
  // the focus fallback below has to tell "no row YET" from "there will never be
  // a row". Set from the promise's continuation, so the commit that carries it
  // also carries whatever the load produced.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    void retryLoad().finally(() => setSettled(true));
  }, [retryLoad]);

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

  const restoreFocusTo = useRowFocusRestore(containerRef);

  /**
   * Land the founder ON the row they were sent to, not merely near it.
   *
   * `Connect {provider}` unmounts the Model tab while its own button holds
   * focus, so focus falls to `<body>` inside a focus-trapped panel: the next Tab
   * restarts at the top of the trap (WCAG 2.4.3), and a screen-reader user is
   * told nothing at all — the reason chip is a `<span>` in a row they were never
   * moved to. The chip's whole audience is the person who cannot see the
   * highlight.
   *
   * **Keyed on the list, not run once on mount — but not for the Connect trip.**
   * `ModelConfigProvider` wraps the whole modal and its load is once-only, so by
   * the time the founder presses Connect the config is already in hand and the
   * rows are in the DOM on this tab's first commit. It is a COLD mount that
   * needs the deferral — a deep link, or a caller from outside the modal, which
   * `openProviderFor` opening the modal deliberately made reachable — where the
   * rows arrive with the fetch and a mount-time lookup finds nothing. Only the
   * tab-level test covers that; the shell test passes either way.
   *
   * **And when there is never a row, focus still has to go somewhere.** A failed
   * load renders a message and a retry and no rows at all, and leaving focus on
   * `<body>` inside the trap is the exact failure this effect exists to fix —
   * reached, on that path, by pressing a button that then unmounted. The panel
   * is the entry point: it holds the message and the retry.
   *
   * **One shot**, and the scenario that needs the ref is not the obvious one. A
   * revoke does not: focus has moved on by then and `focusWasLost` declines. The
   * one that does is arrive on Ollama → collapse it → edit Anthropic → Save: the
   * save unmounts the Save button holding focus, so focus IS lost, and its
   * refetch changes `entries` — without the ref this fires again and drags the
   * founder back to a row they left. The shot is burned even when the move is
   * declined, because a decline means focus is somewhere real.
   */
  const arrivalAnchored = useRef(false);
  useEffect(() => {
    if (!arrivedAt || arrivalAnchored.current) return;
    const anchor = containerRef.current?.querySelector<HTMLElement>(
      rowAnchorSelector(arrivedAt),
    );
    // No row yet and the fetch still running: keep the shot for the row.
    const target = anchor ?? (settled ? containerRef.current : null);
    if (!target) return;
    arrivalAnchored.current = true;
    if (focusWasLost()) target.focus();
  }, [arrivedAt, entries, settled]);

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
      const prompt = removalPrompt(name, dependentBindings(config, provider));
      setPending({ provider, name, prompt });
    },
    [config],
  );

  /**
   * A confirmation belongs to a ROW, so it must not outlive that row being
   * filtered away. Left standing, `pending` survives the row's unmount and the
   * panel REMOUNTS when the row returns — taking focus out of the search box
   * mid-word, on the founder's own keystroke. Answered by the handlers that own
   * the filters, against the NEXT values, rather than by storing a second truth
   * about what is visible.
   */
  const dropPendingIfHidden = useCallback(
    (nextQuery: string, nextFilter: ProviderFilterValue) => {
      setPending((prev) => {
        if (!prev) return prev;
        const item = entries.find((e) => e.status.provider === prev.provider);
        if (!item) return null;
        const inGroup =
          nextFilter === "all" ||
          (nextFilter === "connected") === item.status.configured;
        return inGroup && filterEntries([item], nextQuery).length > 0
          ? prev
          : null;
      });
    },
    [entries],
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
      closeRow();
    },
    [addToast, credentials, closeRow],
  );

  // Every row-level prop is the same for both groups, and the two groups are
  // one list split for reading — so they are passed once, here.
  const rows = (list: readonly ProviderEntry[]) => (
    <ProviderList
      entries={list}
      expanded={expanded}
      pending={pending}
      isBusy={credentials.isBusy}
      onOpen={openRow}
      onClose={closeRow}
      onTest={handleTest}
      onRemove={handleRemove}
      onSave={handleSave}
      onCancelRemoval={closeConfirmation}
      onConfirmRemoval={confirmRemoval}
    />
  );

  return (
    <div ref={containerRef} tabIndex={-1} className="flex flex-col">
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
            onChange={(event) => {
              setQuery(event.target.value);
              dropPendingIfHidden(event.target.value, filter);
            }}
            placeholder={`Search ${entries.length} providers`}
            autoComplete="off"
            spellCheck={false}
            className="flex-1 min-w-0 bg-transparent text-[15px] sm:text-[14px] text-t-primary placeholder:text-t-muted"
          />
        </label>
        <ProviderFilter
          value={filter}
          onChange={(next) => {
            setFilter(next);
            dropPendingIfHidden(query, next);
          }}
        />
      </div>

      {/* The catalog carries every display name and credential schema, so
          without it `buildEntries` gives every provider a null entry and each
          row renders as UNCATALOGUED — no Connect, no Edit, no Test, no form,
          only Remove. Rows still render, so the empty state never appears and
          its retry is unreachable: a tab where no key can be pasted, and one
          transient toast as the only signal. The band is persistent and sits
          above the groups for exactly that case. */}
      {!loading && catalog === null && (
        <div
          role="status"
          className="mb-[14px] flex flex-wrap items-center gap-3 rounded-[var(--radius-lg)] border border-j-warning/35 bg-j-warning-soft px-4 py-3"
        >
          <p className="flex-1 min-w-[200px] text-[12.5px] leading-[1.5] text-t-secondary">
            Provider catalog unavailable — credentials cannot be edited until it
            loads.
          </p>
          <button
            type="button"
            onClick={() => void retryLoad()}
            className="shrink-0 text-[12.5px] font-medium text-j-primary hover:underline cursor-pointer"
          >
            Try again
          </button>
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

      {/* `role="status"`: this container SWAPS its text as a load settles, and a
          screen-reader user parked on "Loading providers…" would otherwise hear
          nothing when it becomes a failure. The retry lives in the band above,
          which renders whenever the catalog is missing — including here. */}
      {!showConnected && !showAvailable && (
        <div role="status" className="py-6 text-center text-[12.5px] text-t-muted">
          {loading
            ? "Loading providers…"
            : config === null
              ? "Providers could not be loaded."
              : entries.length === 0
                ? "No providers available."
                : "No providers match."}
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

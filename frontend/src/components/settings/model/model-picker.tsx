"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { CatalogModel, CatalogProvider, ProviderStatus } from "@/lib/types";
import { CheckIcon, SearchIcon } from "../icons";
import { useOverlayClaim } from "../overlay-context";

/** §9.3 `kbd` — picker only. */
const KBD_CLASS =
  "inline-flex items-center justify-center min-w-[17px] h-[17px] px-[4px] " +
  "rounded-[4px] bg-surface-3 border border-b-primary text-t-tertiary text-[10.5px]";

/** §9.3 `tchip`. */
const TCHIP_CLASS =
  "inline-flex items-center shrink-0 h-[18px] px-[7px] rounded-[5px] " +
  "bg-surface-3 text-t-tertiary text-[10.5px] font-normal";

/** The metadata columns. Fixed widths, so the numbers line up down the list. */
const COL_CLASS = "shrink-0 text-right text-[11.5px] tabular-nums";

const FOCUSABLE_SELECTOR =
  'a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])';

function Kbd({ children }: { children: string }) {
  return <kbd className={KBD_CLASS}>{children}</kbd>;
}

/** Sentence case, per **A3** — nothing on screen may be a raw slug. */
const sentenceCase = (slug: string): string =>
  slug.charAt(0).toUpperCase() + slug.slice(1);

/** `thinking_style` is a provider-shaped slug; the row needs the *behaviour*,
 *  and the provider has its own column. Unknown styles are humanised (**A3**). */
const THINKING_LABELS: Record<string, string> = {
  anthropic_adaptive: "Adaptive",
  anthropic_legacy: "Budgeted",
  openai_effort: "Effort",
  gemini: "Thinking",
  none: "No thinking",
};

const thinkingLabel = (style: string): string =>
  THINKING_LABELS[style] ?? sentenceCase(style.replace(/_/g, " ").trim());

/** 200000 → `200K`. The unit is what makes the 52px column legible. */
function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${Math.round((tokens / 1_000_000) * 10) / 10}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

/** Per-1k stored, per-Mtok shown, so ×1000 — rounded, because `0.005 * 1000`
 *  is not exactly 5 in binary floating point. */
const usdPerMtok = (perThousand: number): string =>
  `$${Math.round(perThousand * 1000 * 100) / 100}`;

const costLabel = (m: CatalogModel): string =>
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

interface Row {
  /** Unique per RENDERED row — a suggested model repeats in its provider's
   *  group, so a bare `model_id` collides and breaks `aria-activedescendant`. */
  id: string;
  model: CatalogModel;
  providerName: string;
}

interface Group {
  key: string;
  title: string;
  /** Only "Suggested" crosses providers, so only it earns the provider column. */
  crossProvider: boolean;
  rows: Row[];
}

function buildGroups(
  uid: string, models: readonly CatalogModel[], providers: readonly CatalogProvider[],
  connected: ReadonlySet<string>, tier: string, terms: readonly string[],
): Group[] {
  // No catalog entry means no display name, and **A3** forbids the slug.
  const nameOf = (slug: string): string =>
    providers.find((p) => p.provider === slug)?.display_name ??
    sentenceCase(slug.replace(/_/g, " "));

  // Only connected providers are offered — binding a tier to one with no
  // credential is §9.6's broken state. They are not hidden: the footer counts
  // them and routes to the Providers tab, which is the point of the footer.
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

export interface ModelPickerProps {
  open: boolean;
  /** Matched against `suggested_tier`; displayed sentence-cased. */
  tier: string;
  /** The bound model, by BOTH keys. `binding-fields.tsx` establishes that a
   *  bare `model_id` is not unique across providers — carrying only the id
   *  ticks the check on the wrong row once two providers share a name. */
  selectedProvider: string | null;
  selectedModelId: string | null;
  models: readonly CatalogModel[];
  providers: readonly CatalogProvider[];
  providerStatuses: readonly ProviderStatus[];
  onSelect: (model: CatalogModel) => void;
  onClose: () => void;
  onBrowseProviders: () => void;
}

/**
 * The §9.9 model picker: a centred command palette over the settings modal.
 *
 * A palette rather than the 261px `<select>` it replaces, because at fifteen
 * providers the decision needs metadata a popover cannot carry — context, price,
 * thinking style — and because that select announced raw slugs (**A3**).
 * Controlled: the panel is MOUNTED only while open, so focus capture and
 * restore ride on mount/unmount rather than on a flag. */
export function ModelPicker(props: ModelPickerProps) {
  // The shell's document-scoped trap stands down while we hold this lease.
  useOverlayClaim(props.open);
  if (!props.open) return null;
  return <PickerPanel {...props} />;
}

function PickerPanel(props: ModelPickerProps) {
  const { tier, selectedProvider, selectedModelId, models, providers } = props;
  const { providerStatuses, onSelect, onClose, onBrowseProviders } = props;
  const uid = useId();
  const listId = `${uid}-list`;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const connected = useMemo(
    () => new Set(providerStatuses.filter((s) => s.configured).map((s) => s.provider)),
    [providerStatuses],
  );
  const terms = useMemo(() => query.toLowerCase().split(/\s+/).filter(Boolean), [query]);
  const groups = useMemo(
    () => buildGroups(uid, models, providers, connected, tier, terms),
    [uid, models, providers, connected, tier, terms],
  );
  const rows = useMemo(() => groups.flatMap((g) => g.rows), [groups]);

  // CLAMPED during render, never corrected in an effect: a filter that shrinks
  // the list must not schedule a second pass to fix the cursor it broke.
  const activeIndex = rows.length === 0 ? -1 : Math.min(active, rows.length - 1);
  const activeId = activeIndex >= 0 ? rows[activeIndex].id : undefined;
  const unconnected = providers.filter((p) => !connected.has(p.provider)).length;
  const tierName = sentenceCase(tier);

  // Focus in on mount, and back to the invoking Model control on close.
  // Restored HERE, not by the parent: `onClose` fires for Escape, a backdrop
  // click and a selection alike, so every tier card would else re-derive which
  // control opened us. Capture must read `activeElement` before we move focus.
  useEffect(() => {
    const previous = document.activeElement;
    const restore =
      previous instanceof HTMLElement && previous !== document.body ? previous : null;
    inputRef.current?.focus();
    return () => restore?.focus();
  }, []);

  // Keep the cursor visible past the fold. jsdom has no `scrollIntoView`.
  useEffect(() => {
    if (!activeId) return;
    document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);

  const choose = (model: CatalogModel) => {
    onSelect(model);
    onClose();
  };

  // Our own trap: `paused` RELEASES the keyboard rather than handing it over,
  // so an overlay that claims the lease must ship one.
  const trapTab = (event: React.KeyboardEvent) => {
    const panel = panelRef.current;
    if (!panel) return;
    const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    if (items.length === 0) return;
    const current = document.activeElement;
    const index = current instanceof HTMLElement ? items.indexOf(current) : -1;
    if (event.shiftKey && index <= 0) {
      event.preventDefault();
      items[items.length - 1].focus();
    } else if (!event.shiftKey && index === items.length - 1) {
      event.preventDefault();
      items[0].focus();
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      // Load-bearing: the shell closes the WHOLE dialog on an unhandled Esc.
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (rows.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((rows.length + activeIndex + step) % rows.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex >= 0) choose(rows[activeIndex].model);
    } else if (event.key === "Tab") {
      trapTab(event);
    }
  };

  return (
    <div className="fixed inset-0 z-[60]">
      <div className="absolute inset-0 bg-surface-0/55" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Choose a model for the ${tierName} tier`}
        onKeyDown={onKeyDown}
        className={
          "absolute inset-0 flex flex-col overflow-hidden bg-surface-1 border border-b-strong " +
          "shadow-[0_24px_60px_rgba(0,0,0,.55)] sm:inset-auto sm:left-1/2 sm:top-[78px] " +
          "sm:h-auto sm:w-[560px] sm:-translate-x-1/2 sm:rounded-[14px]"
        }
      >
        <div className="flex shrink-0 items-center gap-[11px] border-b border-b-secondary px-4 py-[14px]">
          <SearchIcon size={15} className="text-t-muted" />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeId}
            aria-label="Search models"
            autoComplete="off"
            placeholder="Search models by name, provider, context or price"
            value={query}
            // Reset the cursor in the HANDLER, never in an effect on `query`.
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            className="min-w-0 flex-1 bg-transparent text-[15px] text-t-primary outline-none placeholder:text-t-muted"
          />
          <span className="shrink-0 text-[11px] text-t-muted">{tierName}</span>
          <Kbd>esc</Kbd>
        </div>
        <div
          id={listId}
          role="listbox"
          aria-label={`Models for ${tierName}`}
          className="min-h-0 flex-1 overflow-y-auto sm:max-h-[474px] sm:flex-none"
        >
          {rows.length === 0 && (
            <p className="px-4 py-[18px] text-[13px] text-t-muted">No models match.</p>
          )}
          {groups.map((group) => (
            <div key={group.key} role="group" aria-label={group.title}>
              <div className="flex items-center gap-2 px-4 pb-[6px] pt-[11px]">
                <span className="text-[10px] uppercase tracking-[.08em] text-t-muted">
                  {group.title}
                </span>
                <span aria-hidden="true" className="h-px flex-1 bg-b-secondary" />
                <span className="text-[11px] text-t-muted">
                  {group.rows.length} {group.rows.length === 1 ? "model" : "models"}
                </span>
              </div>
              {group.rows.map((row) => (
                <ModelRow
                  key={row.id}
                  row={row}
                  showProvider={group.crossProvider}
                  selected={
                    row.model.provider === selectedProvider &&
                    row.model.model_id === selectedModelId
                  }
                  active={row.id === activeId}
                  onChoose={() => choose(row.model)}
                  onHover={() => setActive(rows.findIndex((r) => r.id === row.id))}
                />
              ))}
            </div>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-3 border-t border-b-secondary bg-surface-2/60 px-4 py-[9px]">
          <div className="flex items-center gap-[5px] text-[11.5px] text-t-muted">
            <Kbd>↑</Kbd> <Kbd>↓</Kbd> <span className="mr-[6px]">navigate</span>
            <Kbd>↵</Kbd> <span>select</span>
          </div>
          <div className="ml-auto flex items-center gap-[10px]">
            {unconnected > 0 && (
              <span className="text-right text-[11.5px] text-t-secondary">
                {unconnected} {unconnected === 1 ? "provider" : "providers"} not connected
              </span>
            )}
            <button
              type="button"
              onClick={onBrowseProviders}
              className={
                "h-[44px] shrink-0 rounded-[var(--radius-md)] bg-j-primary px-[12px] " +
                "text-[13px] font-medium text-j-primary-fg transition-colors " +
                "hover:bg-j-primary-hover cursor-pointer sm:h-[30px]"
              }
            >
              Browse all providers
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface ModelRowProps {
  row: Row;
  showProvider: boolean;
  /** `selected` is the BOUND model — check glyph and 2px rule. `active` is the
   *  keyboard cursor, reported through `aria-activedescendant`, not selection. */
  selected: boolean;
  active: boolean;
  onChoose: () => void;
  onHover: () => void;
}

function ModelRow({ row, showProvider, selected, active, onChoose, onHover }: ModelRowProps) {
  const { model } = row;
  // 1M+ context is the ONE place colour marks a value rather than a state.
  const wide = model.context_window >= 1_000_000;
  // Branch-selected: all three set `background`, and equal-specificity classes
  // would resolve by Tailwind's output order rather than here.
  const fill = selected ? "bg-j-primary-soft" : active ? "bg-surface-2" : "";
  const edge = selected ? "border-l-2 border-l-j-primary pl-[14px]" : "pl-[16px]";
  return (
    <div
      id={row.id}
      role="option"
      aria-selected={selected}
      onClick={onChoose}
      onMouseMove={onHover}
      className={`flex cursor-pointer items-center gap-3 py-[9px] pr-4 text-[13.5px] ${fill} ${edge}`}
    >
      {selected ? (
        <CheckIcon size={13} className="text-j-primary" />
      ) : (
        <span aria-hidden="true" className="w-[13px] shrink-0" />
      )}
      <span className={`min-w-0 flex-1 truncate text-t-primary ${selected ? "font-medium" : ""}`}>
        {model.display_name}
      </span>
      <span className={TCHIP_CLASS}>{thinkingLabel(model.thinking_style)}</span>
      <span className={`${COL_CLASS} w-[52px] ${wide ? "text-j-primary" : "text-t-muted"}`}>
        {formatContext(model.context_window)}
      </span>
      <span className={`${COL_CLASS} w-[96px] text-t-muted`}>{costLabel(model)}</span>
      {showProvider && (
        <span className={`${COL_CLASS} w-[66px] truncate text-t-muted`}>{row.providerName}</span>
      )}
    </div>
  );
}

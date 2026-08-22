"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { CatalogModel, CatalogProvider, ProviderStatus } from "@/lib/types";
import { btn } from "../controls";
import { sentenceCase } from "../labels";
import { useFocusTrap } from "../hooks/use-focus-trap";
import { CheckIcon, SearchIcon } from "../icons";
import { useOverlayClaim } from "../overlay-context";
import {
  buildGroups,
  connectedProviders,
  costLabel,
  formatContext,
  queryTerms,
  thinkingLabel,
  unconnectedLabel,
  type Row,
} from "./model-picker-catalog";

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

function Kbd({ children }: { children: string }) {
  return <kbd className={KBD_CLASS}>{children}</kbd>;
}

const isBound = (row: Row, provider: string | null, id: string | null): boolean =>
  row.model.provider === provider && row.model.model_id === id;

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
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  // The shared trap, not a private copy: it carries the fixed focusable
  // selector, document-order resolution for a focused non-stop, the visibility
  // filter, and the capture → isolate → focus / release → restore ordering that
  // `use-focus-trap.ts` documents as load-bearing. `isolate` is the WRAPPER so
  // the backdrop stays clickable while everything outside goes `inert` — which
  // is what earns the `aria-modal` below rather than merely asserting it.
  // Both traps are armed for one render: a child's effects run before its
  // parent's, so this registers before `useOverlayClaim` pauses the shell's.
  // Self-correcting, and harmless in the direction that matters — that window
  // has two traps competing over Tab, never zero.
  const panelRef = useFocusTrap<HTMLDivElement>({ isolate: wrapperRef });
  const [query, setQuery] = useState("");

  const connected = useMemo(() => connectedProviders(providerStatuses), [providerStatuses]);
  const terms = useMemo(() => queryTerms(query), [query]);
  const groups = useMemo(
    () => buildGroups(uid, models, providers, connected, tier, terms),
    [uid, models, providers, connected, tier, terms],
  );
  const rows = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  // The index each group's first row occupies in `rows`, so a row can be handed
  // its own position instead of scanning for it on every `mousemove`.
  const offsets = useMemo(
    () => groups.map((_, i) => groups.slice(0, i).reduce((n, g) => n + g.rows.length, 0)),
    [groups],
  );

  // Opens ON the bound model, not at the top: reopening a bound tier should put
  // the cursor where the check already is. Declared after the memos on purpose
  // — a lazy initialiser may read them, and this way the first cursor is
  // computed once from the unfiltered list rather than fixed up in an effect.
  const [active, setActive] = useState(() => {
    const bound = rows.findIndex((r) => isBound(r, selectedProvider, selectedModelId));
    return bound < 0 ? 0 : bound;
  });

  // CLAMPED during render, never corrected in an effect: a filter that shrinks
  // the list must not schedule a second pass to fix the cursor it broke.
  const activeIndex = rows.length === 0 ? -1 : Math.min(active, rows.length - 1);
  const activeId = activeIndex >= 0 ? rows[activeIndex].id : undefined;
  const notConnected = unconnectedLabel(providers, connected);
  const tierName = sentenceCase(tier);

  // The trap focuses the panel; this puts the caret in the search field. Runs
  // second because `useFocusTrap` was called first, and a component's effects
  // fire in the order their hooks ran.
  useEffect(() => {
    inputRef.current?.focus();
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

  /**
   * Keys are read at the DOCUMENT, in the CAPTURE phase. Both halves are load-bearing.
   *
   * At the document, because a React handler on the panel only fires while
   * focus is inside the React root: one click on a group header, on whitespace
   * or on the scrollbar blurs the input to `<body>`, and from there Escape
   * never reached this handler, never called `preventDefault()`, and
   * `settings-modal.tsx` — which closes on any Escape it sees unhandled — tore
   * down the whole dialog and every unsaved binding edit.
   *
   * In the capture phase, because the shell's own Escape listener is also on
   * `document` and was registered FIRST (it mounts with the dialog, we mount
   * later). Same node, same phase means registration order, so a bubble-phase
   * listener here would run after the shell had already closed everything.
   */
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // The list owns ↑/↓/↵ everywhere EXCEPT on a focusable control. Scoped
      // to the input alone it closed C2 but re-opened C1: one blur to `<body>`
      // — a mousedown the caret guard below happened not to catch — and the
      // keyboard went dead. Scoping by "is this a control?" instead keeps the
      // footer button's own Enter (C2) while surviving a lost caret, which is
      // what stops that guard from being a single point of failure.
      const target = event.target;
      const onControl =
        target instanceof Element &&
        target !== inputRef.current &&
        target.closest("button,a,select,textarea,[contenteditable]") !== null;
      const inList = !onControl;
      if (event.key === "Escape") {
        // Not checked against the INNERMOST claimant: a future confirm dialog
        // stacked over the palette would have its Escape eaten here. The lease
        // in `overlay-context.ts` already counts claims if that day comes.
        event.preventDefault();
        onClose();
      } else if (inList && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        event.preventDefault();
        if (rows.length === 0) return;
        const step = event.key === "ArrowDown" ? 1 : -1;
        setActive((rows.length + activeIndex + step) % rows.length);
      } else if (inList && event.key === "Enter") {
        event.preventDefault();
        if (activeIndex >= 0) {
          onSelect(rows[activeIndex].model);
          onClose();
        }
      }
      // Tab is the shared trap's, on its own document listener.
    };
    // Re-registered whenever the list or cursor moves rather than reading them
    // through refs — a ref written during render is a render side effect, and
    // add/removeEventListener is cheaper than the bug that buys.
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [rows, activeIndex, onSelect, onClose]);

  return (
    <div ref={wrapperRef} className="fixed inset-0 z-[60]">
      {/* Inside the isolate root, so it is never a marked sibling — an `inert`
          backdrop swallows its own onClick and click-outside dies silently. */}
      <div
        data-testid="model-picker-backdrop"
        className="absolute inset-0 bg-surface-0/55"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Choose a model for the ${tierName} tier`}
        // Keep the caret in the search field, so typing keeps reaching it. No
        // longer load-bearing for ↑/↓/↵ — `inList` above owns that — but still
        // the difference between typing and typing into nothing.
        //
        // `Element`, not `HTMLElement`: an `SVGElement` is neither an
        // `HTMLElement` nor a subclass of one, so that guard skipped the 15px
        // search glyph the founder was aiming past, and the check glyph on
        // every bound row. `closest` exists on both.
        //
        // Two known costs. Text in the palette cannot be drag-selected, so a
        // context window or a price cannot be copied — accepted narrowly,
        // because a dead caret is worse. And a mousedown on the results list's
        // native SCROLLBAR targets that `<div>` and is prevented here; browsers
        // are believed to run a scrollbar drag ahead of the default action, but
        // jsdom renders no scrollbar, so that one needs a live click-through.
        onMouseDown={(event) => {
          const target = event.target;
          if (target instanceof Element && !target.closest("input,button,a,select,textarea")) {
            event.preventDefault();
          }
        }}
        className={
          "absolute inset-0 flex flex-col overflow-hidden bg-surface-1 border border-b-strong " +
          "shadow-[0_24px_60px_rgba(0,0,0,.55)] outline-none sm:inset-auto sm:left-1/2 " +
          "sm:top-[78px] sm:h-auto sm:w-[560px] sm:-translate-x-1/2 sm:rounded-[14px]"
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
        {rows.length === 0 && (
          // Outside the listbox: it is neither an option nor a group, and a
          // listbox whose only child is prose announces as a broken list.
          <p role="status" className="px-4 py-[18px] text-[13px] text-t-muted">
            No models match.
          </p>
        )}
        <div
          id={listId}
          role="listbox"
          aria-label={`Models for ${tierName}`}
          className="min-h-0 flex-1 overflow-y-auto sm:max-h-[474px] sm:flex-none"
        >
          {groups.map((group, groupIndex) => (
            <div key={group.key} role="group" aria-label={group.title}>
              <div className="flex items-center gap-2 px-4 pb-[6px] pt-[11px]">
                <span className="text-[10px] font-medium uppercase tracking-[.08em] text-t-muted">
                  {group.title}
                </span>
                <span aria-hidden="true" className="h-px flex-1 bg-b-secondary" />
                <span className="text-[11px] tabular-nums text-t-muted">
                  {group.rows.length} {group.rows.length === 1 ? "model" : "models"}
                </span>
              </div>
              {group.rows.map((row, rowIndex) => (
                <ModelRow
                  key={row.id}
                  row={row}
                  index={offsets[groupIndex] + rowIndex}
                  showProvider={group.crossProvider}
                  selected={isBound(row, selectedProvider, selectedModelId)}
                  active={row.id === activeId}
                  onChoose={choose}
                  onHover={setActive}
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
            {notConnected && (
              <span className="text-right text-[11.5px] tabular-nums text-t-secondary">
                {notConnected}
              </span>
            )}
            <button type="button" onClick={onBrowseProviders} className={btn({ size: "sm", variant: "primary" })}>
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
  /** This row's position in the flattened list — handed down rather than
   *  re-derived, because `onMouseMove` fires on every pixel of travel. */
  index: number;
  showProvider: boolean;
  /** `selected` is the BOUND model — check glyph and 2px rule. `active` is the
   *  keyboard cursor, reported through `aria-activedescendant`, not selection. */
  selected: boolean;
  active: boolean;
  onChoose: (model: CatalogModel) => void;
  onHover: (index: number) => void;
}

function ModelRow({ row, index, showProvider, selected, active, onChoose, onHover }: ModelRowProps) {
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
      onClick={() => onChoose(model)}
      onMouseMove={() => onHover(index)}
      // §9.9 fixes the row at `9px` vertical, which is a 36px target — under
      // §9.10's 44px minimum for the one act this whole surface exists for.
      // Padded to 44px below `sm` only; the desktop row is §9.9's to the pixel.
      className={`flex cursor-pointer items-center gap-3 py-[12px] pr-4 text-[13.5px] sm:py-[9px] ${fill} ${edge}`}
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

"use client";

import { useId, useMemo, useState } from "react";

import type {
  AgentInfo,
  CatalogModel,
  CatalogProvider,
  ConfigWarning,
  ModelBinding,
} from "@/lib/types";
import { LABEL_CLASS, btn, ctl } from "../controls";
import { ChevronDownIcon, ChevronRightIcon, WarningIcon } from "../icons";
import { BindingFields, type BindingPatch } from "./binding-fields";

/** What a brand-new override starts at when the agent's tier binding is missing
 *  — the one case where nothing sensible can be copied. Never a provider guess:
 *  an empty `model_id` renders "Select a model…" and sends the founder to the
 *  picker, which is the only control that can set provider and model together. */
const BARE_SEED = {
  provider: "",
  model_id: "",
  effort: "none",
  max_tokens: 4096,
  temperature: null,
} as const satisfies Omit<ModelBinding, "scope_type" | "scope_key">;

/**
 * A new override for `agent`, seeded from the tier it rides on today.
 *
 * Seeded rather than blank because an override's *purpose* is to differ from the
 * tier in one field — effort, or max tokens — and a blank row would make the
 * founder re-choose the model they were already running just to change a number.
 */
export function seedOverride(
  agent: AgentInfo,
  tiers: readonly ModelBinding[],
): ModelBinding {
  const tier = tiers.find((t) => t.scope_key === agent.tier);
  const base = tier
    ? {
        provider: tier.provider,
        model_id: tier.model_id,
        effort: tier.effort,
        max_tokens: tier.max_tokens,
        temperature: tier.temperature,
      }
    : BARE_SEED;
  return { scope_type: "agent", scope_key: agent.name, ...base };
}

export interface AgentOverridesProps {
  /** The draft's `agent_overrides`, rendered in draft order. */
  overrides: readonly ModelBinding[];
  /** Every agent the catalog knows — the add-selector's candidate pool, and the
   *  source of each override's display name and default tier. */
  agents: readonly AgentInfo[];
  /** The draft's tier bindings. Read only to SEED a new override. */
  tiers: readonly ModelBinding[];
  models: readonly CatalogModel[];
  providers: readonly CatalogProvider[];
  /** Whole-section disable, e.g. mid-save. */
  disabled?: boolean;
  /** This override differs from the saved one. A predicate rather than the
   *  `dirtyKeys` set, so this component holds no knowledge of the key format. */
  dirty: (scopeKey: string) => boolean;
  /** A 422 the server returned for this override. Same rule as the tier cards:
   *  rendered where the refused binding is, never as a toast. */
  rejection: (scopeKey: string) => ConfigWarning | undefined;
  onChange: (scopeKey: string, patch: BindingPatch) => void;
  /** Receives a fully-seeded binding. The parent upserts it into the draft. */
  onAdd: (binding: ModelBinding) => void;
  onRemove: (scopeKey: string) => void;
  onOpenPicker: (scopeKey: string) => void;
}

/**
 * Per-agent model overrides, as a collapsed disclosure with an active count.
 *
 * Collapsed by default and counted in its own summary, because an override is
 * the exception: the tier cards above are what a founder came to change, and a
 * list of six agents expanded underneath them buries the three controls that
 * matter. The count is what makes collapsing safe — a workspace with overrides
 * in force says so without being opened.
 *
 * It holds no draft state. Every edit leaves as a patch and every structural
 * change as an `onAdd`/`onRemove`, so the hook that owns the draft stays the
 * only writer — including for the one save affordance, which is the tab's
 * (§9.7). There is deliberately no Save button in here.
 */
export function AgentOverrides({
  overrides,
  agents,
  tiers,
  models,
  providers,
  disabled = false,
  dirty,
  rejection,
  onChange,
  onAdd,
  onRemove,
  onOpenPicker,
}: AgentOverridesProps) {
  const uid = useId();
  const panelId = `${uid}-panel`;
  const selectId = `${uid}-agent`;

  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  /** The refusal below, said out loud. `null` whenever the last add succeeded. */
  const [refusal, setRefusal] = useState<string | null>(null);

  const overridden = useMemo(
    () => new Set(overrides.map((o) => o.scope_key)),
    [overrides],
  );
  /** Only agents WITHOUT an override are offered — `upsertBinding` replaces
   *  silently, so an agent already in the list must not be selectable at all. */
  const candidates = useMemo(
    () => agents.filter((a) => !overridden.has(a.name)),
    [agents, overridden],
  );

  const displayName = (scopeKey: string): string =>
    agents.find((a) => a.name === scopeKey)?.display_name ?? scopeKey;

  const add = () => {
    const agent = agents.find((a) => a.name === choice);
    if (!agent) return;
    // The selector cannot offer an overridden agent, so this is unreachable
    // through the UI — and it is here precisely because `upsertBinding` would
    // not complain. A silently overwritten override is an edit the founder
    // never made to a binding they cannot see; a refusal is one they can.
    if (overridden.has(agent.name)) {
      setRefusal(`${agent.display_name} already has an override.`);
      return;
    }
    setRefusal(null);
    setChoice("");
    onAdd(seedOverride(agent, tiers));
  };

  return (
    <section aria-labelledby={`${uid}-title`}>
      <button
        type="button"
        id={`${uid}-title`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={
          "flex items-center gap-[7px] w-full text-left text-[12.5px] " +
          "text-t-secondary hover:text-t-primary transition-colors cursor-pointer"
        }
      >
        {open ? (
          <ChevronDownIcon size={12} className="text-t-tertiary" />
        ) : (
          <ChevronRightIcon size={12} className="text-t-tertiary" />
        )}
        <span className="font-medium">Per-agent overrides</span>
        <span className="text-t-muted tabular-nums">
          {overrides.length} active
        </span>
      </button>

      {open && (
        <div id={panelId} className="mt-[12px] flex flex-col gap-[10px]">
          {overrides.length === 0 ? (
            <p className="text-[12px] text-t-muted">
              No overrides. Every agent runs on its tier&apos;s model.
            </p>
          ) : (
            overrides.map((override) => {
              const refused = rejection(override.scope_key);
              // The agent's name IS the row's identity, exactly as a tier card
              // heads itself with its own `scope_key` — so there is no second
              // string that could name an agent the grid below is not editing.
              const headingId = `${uid}-${override.scope_key}`;
              return (
                <section
                  key={override.scope_key}
                  aria-labelledby={headingId}
                  className="bg-surface-1 border border-b-secondary rounded-[var(--radius-lg)] pt-[13px] px-[20px] pb-[13px]"
                >
                  <div className="flex items-center justify-between gap-3 mb-[11px]">
                    <h4
                      id={headingId}
                      className="text-[13px] font-semibold tracking-[.02em] text-t-primary truncate"
                    >
                      {displayName(override.scope_key)}
                    </h4>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onRemove(override.scope_key)}
                      className={btn({ size: "sm", variant: "danger" })}
                    >
                      Remove
                    </button>
                  </div>

                  <BindingFields
                    binding={override}
                    models={models}
                    providers={providers}
                    disabled={disabled}
                    dirty={dirty(override.scope_key)}
                    warning={refused !== undefined}
                    onChange={(patch) => onChange(override.scope_key, patch)}
                    onOpenPicker={() => onOpenPicker(override.scope_key)}
                  />

                  {refused && (
                    <p
                      role="alert"
                      className="mt-[10px] flex items-center gap-[9px] text-[12px] text-j-warning"
                    >
                      <WarningIcon
                        size={14}
                        className="text-j-warning shrink-0"
                      />
                      {refused.message}
                    </p>
                  )}
                </section>
              );
            })
          )}

          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1 sm:max-w-[240px]">
              <label htmlFor={selectId} className={LABEL_CLASS}>
                Add an override
              </label>
              <div className="relative">
                <select
                  id={selectId}
                  value={choice}
                  disabled={disabled || candidates.length === 0}
                  onChange={(e) => setChoice(e.target.value)}
                  className={ctl({
                    extra:
                      "appearance-none pr-[26px] cursor-pointer " +
                      "disabled:cursor-default disabled:opacity-45",
                  })}
                >
                  <option value="">
                    {candidates.length === 0
                      ? "Every agent is overridden"
                      : "Select an agent…"}
                  </option>
                  {candidates.map((agent) => (
                    <option key={agent.name} value={agent.name}>
                      {agent.display_name}
                    </option>
                  ))}
                </select>
                <ChevronDownIcon className="pointer-events-none absolute right-[10px] top-1/2 -translate-y-1/2 text-t-tertiary" />
              </div>
            </div>
            <button
              type="button"
              disabled={disabled || !choice}
              onClick={add}
              className={btn({ size: "md" })}
            >
              Add override
            </button>
          </div>

          {refusal && (
            <p role="alert" className="text-[12px] text-j-warning">
              {refusal}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

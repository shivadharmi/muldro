"use client";

import { useId } from "react";

import type { CatalogModel, CatalogProvider, ModelBinding } from "@/lib/types";

/**
 * The subset of a binding this grid can edit.
 *
 * The model is chosen in the picker (§9.9) and the provider is *derived* from
 * it (§2.3) — neither is typed here — so a patch leaving this component can only
 * ever carry these three keys. Emitting a patch rather than a whole `ModelBinding`
 * keeps the owning draft the single writer of `scope_*`, `provider` and
 * `model_id`: this component cannot reconstruct a field it does not render, and
 * therefore cannot silently blank one. That is the shape of defect **F1** — a
 * control that rewrote `model_id` as a side effect of touching its neighbour.
 */
export type BindingPatch = Partial<
  Pick<ModelBinding, "effort" | "max_tokens" | "temperature">
>;

const EFFORT_OPTIONS: readonly ModelBinding["effort"][] = [
  "none",
  "low",
  "medium",
  "high",
];

/** §9.3 `ctl` metrics. 44px/15px/12px below `sm` (touch target), 36px/14px/10px above. */
const CTL_BASE =
  "w-full h-[44px] sm:h-[36px] px-[12px] sm:px-[10px] " +
  "rounded-[var(--radius-md)] border transition-colors";

const CTL_ENABLED = "bg-surface-2 text-t-primary text-[15px] sm:text-[14px]";

/** §9.3 `ctl-off`. A control a model does not support is DISABLED, never
 *  unmounted (**F4**) — unmounting reflowed the row every time the model changed. */
const CTL_OFF =
  "bg-surface-2/45 border-dashed border-b-secondary text-t-muted text-[12px] " +
  "cursor-not-allowed";

const BORDER_IDLE = "border-b-secondary";
const BORDER_DIRTY =
  "border-j-primary shadow-[0_0_0_1px_var(--muldro-primary-soft)]";
/** §9.6: on a warned card only the Model control's border changes — no reflow. */
const BORDER_WARNING = "border-j-warning/45";

/** §9.3 `ctl-lbl`. VISIBLE, above every control — the fix for **L2**. */
const LABEL_CLASS =
  "block text-[10px] font-medium uppercase text-t-muted tracking-[.07em] " +
  "mb-[6px] sm:mb-[5px]";

interface CtlOptions {
  off?: boolean;
  dirty?: boolean;
  warning?: boolean;
  extra?: string;
}

/**
 * Composed rather than concatenated, because the mutually exclusive parts each
 * set the SAME property. `text-[12px]` and `text-[14px]`, or `border-j-primary`
 * and `border-b-secondary`, have equal CSS specificity — which of them wins would
 * be decided by Tailwind's output order, not by this file. Selecting one branch
 * makes the outcome explicit.
 */
function ctl({ off, dirty, warning, extra }: CtlOptions): string {
  if (off) return `${CTL_BASE} ${CTL_OFF}`;
  const border = warning ? BORDER_WARNING : dirty ? BORDER_DIRTY : BORDER_IDLE;
  return `${CTL_BASE} ${CTL_ENABLED} ${border}${extra ? ` ${extra}` : ""}`;
}

/** §9.11. A SEARCH glyph, not a chevron: the Model control opens a command
 *  palette, not a dropdown, and the affordance has to say which. */
function SearchIcon() {
  return (
    <svg
      viewBox="0 0 12 12"
      width={11}
      height={11}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0 text-t-tertiary"
    >
      <circle cx="5.2" cy="5.2" r="3.3" />
      <path d="M7.7 7.7l2.1 2.1" />
    </svg>
  );
}

/** §9.11 chevron-down, for the one real `<select>` in the grid. */
function ChevronDownIcon() {
  return (
    <svg
      viewBox="0 0 10 10"
      width={10}
      height={10}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.3}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="pointer-events-none absolute right-[10px] top-1/2 -translate-y-1/2 text-t-tertiary"
    >
      <path d="M2.5 4l2.5 2.5L7.5 4" />
    </svg>
  );
}

interface FieldProps {
  id: string;
  labelId?: string;
  label: string;
  className?: string;
  children: React.ReactNode;
}

/** One labelled cell. `htmlFor`/`id` is what makes the visible label the control's
 *  programmatic name, rather than a decoration sitting next to an `aria-label`. */
function Field({ id, labelId, label, className, children }: FieldProps) {
  return (
    <div className={className}>
      <label id={labelId} htmlFor={id} className={LABEL_CLASS}>
        {label}
      </label>
      {children}
    </div>
  );
}

/** The binding's model, identified by BOTH keys — a bare `model_id` is not unique
 *  across providers. `undefined` for an empty or de-listed binding. */
function findModel(
  models: readonly CatalogModel[],
  binding: ModelBinding,
): CatalogModel | undefined {
  return models.find(
    (m) => m.provider === binding.provider && m.model_id === binding.model_id,
  );
}

export interface BindingFieldsProps {
  /** The binding being edited. Never mutated — every edit leaves as a patch. */
  binding: ModelBinding;
  /** The full catalog model list. Passed whole rather than pre-resolved so this
   *  component owns the (provider, model_id) identity rule in one place. */
  models: readonly CatalogModel[];
  /** Catalog providers, for the DERIVED provider's display name. A provider the
   *  catalog no longer lists falls back to its slug rather than rendering blank. */
  providers: readonly CatalogProvider[];
  /** Receives only what changed. See `BindingPatch`. */
  onChange: (patch: BindingPatch) => void;
  /** Opens the model picker. This grid has no model dropdown and no provider
   *  control at all — that pair is what made **F1** possible. */
  onOpenPicker: () => void;
  /** This binding differs from the saved one. Marks the editable controls per §9.3. */
  dirty?: boolean;
  /** Whole-grid disable (e.g. mid-save). Distinct from `ctl-off`, which is a
   *  per-control statement about model capability. */
  disabled?: boolean;
  /** The bound provider has no credential (§9.6). Recolours the Model control
   *  only; the card owns the warning row itself. */
  warning?: boolean;
}

/**
 * The four-field control grid that edits one `ModelBinding` — used by the tier
 * cards and by the per-agent overrides list alike (§4.3, §9.5).
 *
 * Purely presentational: it holds no state, fetches nothing, and never decides
 * what a change means. It renders exactly four cells in a CSS grid that cannot
 * wrap (**L1**), each with a visible label (**L2**), and it disables rather than
 * unmounts a control the selected model does not support (**F4**, **B4**).
 */
export function BindingFields({
  binding,
  models,
  providers,
  onChange,
  onOpenPicker,
  dirty = false,
  disabled = false,
  warning = false,
}: BindingFieldsProps) {
  const uid = useId();
  const ids = {
    model: `${uid}-model`,
    modelLabel: `${uid}-model-lbl`,
    modelValue: `${uid}-model-val`,
    effort: `${uid}-effort`,
    maxTokens: `${uid}-max-tokens`,
    temperature: `${uid}-temperature`,
  };

  const selectedModel = findModel(models, binding);

  // Derived, never selected. The provider is a FACT about the chosen model, so
  // there is nothing here that could blank `model_id` (**F1**).
  const providerSlug = selectedModel?.provider ?? binding.provider;
  const providerName =
    providers.find((p) => p.provider === providerSlug)?.display_name ??
    providerSlug;

  // A model that has left the catalog still renders its id, never an empty box.
  const modelLabel =
    selectedModel?.display_name || binding.model_id || "Select a model…";

  // Both defaults are FAIL-CLOSED: with no model resolved we assume neither
  // capability, so the founder is never offered a control the backend will reject.
  const effortOff = selectedModel?.thinking_style === "none" || !selectedModel;
  const temperatureOff = !selectedModel?.accepts_temperature;

  return (
    <div className="grid grid-cols-2 gap-[10px] sm:grid-cols-[2.7fr_1fr_1.1fr_1.1fr] sm:gap-[12px]">
      {/* Model — spans both columns below `sm` (§9.5). */}
      <Field
        id={ids.model}
        labelId={ids.modelLabel}
        label="Model"
        className="col-span-2 sm:col-span-1"
      >
        <button
          id={ids.model}
          type="button"
          aria-haspopup="dialog"
          // Name = the visible label PLUS the current value, so the control is
          // announced as "Model, <name>" rather than as an anonymous button.
          aria-labelledby={`${ids.modelLabel} ${ids.modelValue}`}
          disabled={disabled}
          onClick={onOpenPicker}
          className={ctl({
            dirty,
            warning,
            extra:
              "flex items-center justify-between gap-2 text-left " +
              "cursor-pointer disabled:cursor-default disabled:opacity-45",
          })}
        >
          <span id={ids.modelValue} className="truncate">
            {modelLabel}
          </span>
          <span className="flex items-center gap-[7px] shrink-0">
            {binding.model_id && (
              <span
                className={`text-[11.5px] ${warning ? "text-j-warning" : "text-t-muted"}`}
              >
                {providerName}
              </span>
            )}
            <SearchIcon />
          </span>
        </button>
      </Field>

      {/* Effort — disabled, not unmounted, when the model does not think (**B4**). */}
      <Field id={ids.effort} label="Effort">
        {effortOff ? (
          <input
            id={ids.effort}
            type="text"
            readOnly
            disabled
            value="n/a"
            className={ctl({ off: true })}
          />
        ) : (
          <div className="relative">
            <select
              id={ids.effort}
              value={binding.effort}
              disabled={disabled}
              onChange={(e) =>
                onChange({ effort: e.target.value as ModelBinding["effort"] })
              }
              className={ctl({
                dirty,
                extra:
                  "appearance-none pr-[26px] cursor-pointer " +
                  "disabled:cursor-default disabled:opacity-45",
              })}
            >
              {EFFORT_OPTIONS.map((effort) => (
                <option key={effort} value={effort}>
                  {effort}
                </option>
              ))}
            </select>
            <ChevronDownIcon />
          </div>
        )}
      </Field>

      <Field id={ids.maxTokens} label="Max tokens">
        <input
          id={ids.maxTokens}
          type="number"
          inputMode="numeric"
          min={1}
          value={binding.max_tokens}
          disabled={disabled}
          // Never persist 0 — the backend rejects `max_tokens < 1`, so a cleared
          // field must land on the floor rather than on an unsavable draft.
          onChange={(e) =>
            onChange({ max_tokens: Math.max(1, Number(e.target.value) || 1) })
          }
          className={ctl({ dirty, extra: "tabular-nums disabled:opacity-45" })}
        />
      </Field>

      {/* Temperature — spans both columns below `sm` (§9.5). */}
      <Field
        id={ids.temperature}
        label="Temperature"
        className="col-span-2 sm:col-span-1"
      >
        {temperatureOff ? (
          <input
            id={ids.temperature}
            type="text"
            readOnly
            disabled
            value="Not accepted"
            className={ctl({ off: true })}
          />
        ) : (
          <input
            id={ids.temperature}
            type="number"
            inputMode="decimal"
            min={0}
            max={2}
            step={0.1}
            value={binding.temperature ?? ""}
            disabled={disabled}
            onChange={(e) =>
              onChange({
                temperature: e.target.value === "" ? null : Number(e.target.value),
              })
            }
            className={ctl({ dirty, extra: "tabular-nums disabled:opacity-45" })}
          />
        )}
      </Field>
    </div>
  );
}

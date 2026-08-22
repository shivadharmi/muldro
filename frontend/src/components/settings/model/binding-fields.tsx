"use client";

import { useId, useState, type ReactNode } from "react";

import type { CatalogModel, CatalogProvider, ModelBinding } from "@/lib/types";
import { LABEL_CLASS, ctl } from "../controls";
import { ChevronDownIcon, SearchIcon } from "../icons";

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

/** Sentence case, per **A3** — a select must not announce a raw slug. */
const EFFORT_LABELS: Record<ModelBinding["effort"], string> = {
  none: "None",
  low: "Low",
  medium: "Medium",
  high: "High",
};

/** §4.3's two fixed strings. Each is a claim about a KNOWN capability, so
 *  neither may be shown for a model whose capabilities we cannot look up. */
const EFFORT_UNSUPPORTED = "n/a";
const TEMPERATURE_UNSUPPORTED = "Not accepted";
/** What a null temperature reads as when the control is not editable. */
const TEMPERATURE_UNSET = "—";

/**
 * A COMPLETE non-negative decimal literal — the only shape Temperature emits.
 *
 * `"0."` is deliberately excluded even though `Number("0.")` is a perfectly
 * finite `0`: it is the halfway state of typing `"0.7"`, not a value, and
 * emitting `0` there would push a sampling temperature the founder never chose.
 * `""`, `"."`, `"-"`, `"1e"` and `"-0.5"` are excluded too, which is where the
 * "no negative temperature" guard now lives — a real check, rather than the
 * `min={0}` attribute it replaces, which never blocked typed input.
 */
const COMPLETE_DECIMAL = /^\d*\.?\d+$/;

/** Narrow a select's raw string back to the union without an `as`. The options
 *  come from `EFFORT_OPTIONS`, so the fallback is unreachable — but it is the
 *  lookup, not an assertion, that makes that true. */
function toEffort(
  value: string,
  fallback: ModelBinding["effort"],
): ModelBinding["effort"] {
  return EFFORT_OPTIONS.find((option) => option === value) ?? fallback;
}

interface FieldProps {
  uid: string;
  /** Also the id stem, so the label and its control cannot drift apart. */
  name: string;
  label: string;
  className?: string;
  children: (ids: { id: string; labelId: string }) => ReactNode;
}

/**
 * One labelled cell.
 *
 * The ids are DERIVED here and handed to the child rather than written at both
 * ends, because `htmlFor`/`id` is the whole of the **L2** guarantee: a typo in
 * one of two hand-written strings breaks the association silently, leaving a
 * label that still looks correct on screen and a control with no name at all.
 */
function Field({ uid, name, label, className, children }: FieldProps) {
  const id = `${uid}-${name}`;
  const labelId = `${id}-lbl`;
  return (
    <div className={className}>
      <label id={labelId} htmlFor={id} className={LABEL_CLASS}>
        {label}
      </label>
      {children({ id, labelId })}
    </div>
  );
}

/** The binding's model, identified by BOTH keys — a bare `model_id` is not
 *  unique across providers. `undefined` for an empty or de-listed binding. */
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
 * Purely presentational: it holds no binding state, fetches nothing, and never
 * decides what a change means. It renders exactly four cells in a CSS grid that
 * cannot wrap (**L1**), each with a visible label (**L2**), and it disables
 * rather than unmounts a control the selected model does not support
 * (**F4**, **B4**).
 *
 * **Querying it in tests.** The Model control's accessible name is
 * `"<label> <value>"` — `aria-labelledby` points at the visible label *and* the
 * rendered model name, so assistive tech announces "Model, Claude Opus 4.5"
 * rather than an anonymous button. `getByLabelText("Model")` (exact) therefore
 * does NOT match it; use `getByLabelText(/^Model/)`. There is deliberately no
 * `aria-label` anywhere in this file: `aria-label` *overrides* a `<label>` as
 * the accessible name, so carrying both would re-open **L2** for anyone using a
 * screen reader while still looking correct in the DOM.
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

  /**
   * The in-flight text of each numeric field, or `null` when not being edited.
   *
   * A controlled numeric input hands whatever it emits straight back down as
   * its own displayed value, so any handler that maps an unparseable keystroke
   * onto a "safe" value rewrites the field under the founder's cursor. Both
   * fields therefore hold their raw text here and emit a patch only for a
   * string that is genuinely complete; `onBlur` discards the draft, so a
   * half-typed value can never be committed.
   *
   * The two are NOT the same problem, and an earlier version of this comment
   * got the distinction wrong by framing it as "does the field have a legal
   * empty state":
   *
   *   * **Max tokens** is an integer, so no valid input passes through an
   *     unparseable prefix. `""` there means "cleared" or "garbage", and both
   *     want the same answer — emit nothing — so collapsing them is correct.
   *     Its draft protects the DISPLAY.
   *   * **Temperature** is a decimal, and typing one goes *through* an
   *     incomplete literal every time: `"0."` is the halfway state of `"0.7"`,
   *     not a value. `""` there means "cleared" (emit `null`) OR "mid-decimal"
   *     (emit nothing) — two different answers — so its draft protects the
   *     MEANING, and the raw string must survive to tell them apart.
   *
   * That last point is why Temperature is the one `type="text"` control here.
   * `<input type="number">` applies HTML value sanitization, so `.value` reads
   * `""` for a raw `"0."` and collapses the two meanings before any handler can
   * see them; the browser's discriminator, `validity.badInput`, is hard-coded
   * `false` in jsdom, so that branch could never be tested. `inputMode="decimal"`
   * keeps the mobile decimal keypad, the only part of `type="number"` this
   * control actually needs.
   */
  const [tokenDraft, setTokenDraft] = useState<string | null>(null);
  const [temperatureDraft, setTemperatureDraft] = useState<string | null>(null);

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

  /**
   * Three states per capability control, not two.
   *
   * "The model says no" and "we cannot look this model up" both disable the
   * control, but they must not say the same thing. `n/a` and `Not accepted` are
   * §4.3's claims about a KNOWN capability; printing them for a retired model
   * asserts something we have not established, and buries the value that is
   * actually stored on the binding. An unresolvable model shows its stored value
   * instead — disabled, because we cannot offer choices we cannot validate.
   */
  const known = selectedModel !== undefined;
  const effortOff = !known || selectedModel.thinking_style === "none";
  const temperatureOff = !known || !selectedModel.accepts_temperature;
  const effortOffValue = known
    ? EFFORT_UNSUPPORTED
    : EFFORT_LABELS[binding.effort];
  const temperatureOffValue = known
    ? TEMPERATURE_UNSUPPORTED
    : (binding.temperature ?? TEMPERATURE_UNSET);

  return (
    <div className="grid grid-cols-2 gap-[10px] sm:grid-cols-[2.7fr_1fr_1.1fr_1.1fr] sm:gap-[12px]">
      {/* Model — spans both columns below `sm` (§9.5). */}
      <Field
        uid={uid}
        name="model"
        label="Model"
        className="col-span-2 sm:col-span-1"
      >
        {({ id, labelId }) => (
          <button
            id={id}
            type="button"
            aria-haspopup="dialog"
            aria-labelledby={`${labelId} ${id}-val`}
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
            <span id={`${id}-val`} className="truncate">
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
              <SearchIcon size={11} className="text-t-tertiary" />
            </span>
          </button>
        )}
      </Field>

      <Field uid={uid} name="effort" label="Effort">
        {({ id }) =>
          effortOff ? (
            <input
              id={id}
              type="text"
              readOnly
              disabled
              value={effortOffValue}
              className={ctl({ off: true })}
            />
          ) : (
            <div className="relative">
              <select
                id={id}
                value={binding.effort}
                disabled={disabled}
                onChange={(e) =>
                  onChange({ effort: toEffort(e.target.value, binding.effort) })
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
                    {EFFORT_LABELS[effort]}
                  </option>
                ))}
              </select>
              <ChevronDownIcon className="pointer-events-none absolute right-[10px] top-1/2 -translate-y-1/2 text-t-tertiary" />
            </div>
          )
        }
      </Field>

      <Field uid={uid} name="max-tokens" label="Max tokens">
        {({ id }) => (
          <input
            id={id}
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            value={tokenDraft ?? String(binding.max_tokens)}
            disabled={disabled}
            // Emit ONLY a whole number the backend can store (`int`, `>= 1`).
            // Anything else — empty, fractional, zero — is held as text and
            // never becomes a patch, so an intermediate keystroke can neither
            // dirty the binding nor be saved. Blur discards it.
            onChange={(e) => {
              const raw = e.target.value;
              setTokenDraft(raw);
              const parsed = Number(raw);
              if (raw !== "" && Number.isInteger(parsed) && parsed >= 1) {
                onChange({ max_tokens: parsed });
              }
            }}
            onBlur={() => setTokenDraft(null)}
            className={ctl({ dirty, extra: "tabular-nums disabled:opacity-45" })}
          />
        )}
      </Field>

      {/* Temperature — spans both columns below `sm` (§9.5). */}
      <Field
        uid={uid}
        name="temperature"
        label="Temperature"
        className="col-span-2 sm:col-span-1"
      >
        {({ id }) =>
          temperatureOff ? (
            <input
              id={id}
              type="text"
              readOnly
              disabled
              value={temperatureOffValue}
              className={ctl({ off: true })}
            />
          ) : (
            <input
              id={id}
              // `text`, not `number` — see the draft docblock above for why.
              type="text"
              inputMode="decimal"
              // The range guard lives in the emit rule below, not in `min`/`max`:
              // those never blocked typed input (they only set `:invalid`) and on
              // a text input they do nothing at all. Still no ceiling — the valid
              // maximum is provider-specific (1 for Anthropic, 2 for OpenAI) and
              // `CatalogModel` carries no range to read it from, so any number
              // here would mislabel one provider's legal value. Same policy as
              // Max tokens: refuse only what is invalid for EVERY provider and
              // let the server's 422 surface the rest (§4.4).
              value={temperatureDraft ?? (binding.temperature ?? "")}
              disabled={disabled}
              onChange={(e) => {
                const raw = e.target.value;
                setTemperatureDraft(raw);
                // Cleared is a VALUE (`null` is legal); an incomplete literal is
                // a state. Only the first emits.
                if (raw === "") {
                  onChange({ temperature: null });
                } else if (COMPLETE_DECIMAL.test(raw)) {
                  onChange({ temperature: Number(raw) });
                }
              }}
              onBlur={() => setTemperatureDraft(null)}
              className={ctl({ dirty, extra: "tabular-nums disabled:opacity-45" })}
            />
          )
        }
      </Field>
    </div>
  );
}

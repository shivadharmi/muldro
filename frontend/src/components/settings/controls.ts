/**
 * The §9.3 control primitives, in one place.
 *
 * These metrics were already triplicated across the settings surface
 * (`model/binding-fields.tsx`, `providers/provider-credential-form.tsx`,
 * `tabs/providers-tab.tsx`) before anyone had written a fourth copy — so the
 * next tweak to a control height meant finding every literal. This module is the
 * single definition; new settings controls import it rather than restate it.
 * The two files above are held by other implementers and are migrated later.
 */

/** §9.3 `ctl` metrics. 44px/15px/12px below `sm` (the touch-target minimum),
 *  36px/14px/10px above. Geometry only — no colour, because each state variant
 *  below replaces a different part of the palette. */
export const CTL_BASE =
  "w-full h-[44px] sm:h-[36px] px-[12px] sm:px-[10px] " +
  "rounded-[var(--radius-md)] border transition-colors";

/** The normal, editable fill. */
export const CTL_ENABLED =
  "bg-surface-2 text-t-primary text-[15px] sm:text-[14px]";

/** §9.3 `ctl-off`. A control the selected model does not support renders
 *  DISABLED, never unmounted — unmounting reflows the row on every model
 *  change, which is defect **F4**. */
export const CTL_OFF =
  "bg-surface-2/45 border-dashed border-b-secondary text-t-muted text-[12px] " +
  "cursor-not-allowed";

export const BORDER_IDLE = "border-b-secondary";
/** §9.3 changed-control border. */
export const BORDER_DIRTY = "border-j-primary";
/** §9.6 warned-binding border — the bound provider has no credential. */
export const BORDER_WARNING = "border-j-warning/45";
/** §9.3 changed-control ring. A `box-shadow`, deliberately split from the border
 *  COLOUR above: the two signals occupy different CSS properties, so a control
 *  can be both warned and changed without either silencing the other. */
export const RING_DIRTY = "shadow-[0_0_0_1px_var(--muldro-primary-soft)]";

/** §9.3 `ctl-lbl`. VISIBLE, above every control — the fix for **L2**. */
export const LABEL_CLASS =
  "block text-[10px] font-medium uppercase text-t-muted tracking-[.07em] " +
  "mb-[6px] sm:mb-[5px]";

export interface CtlOptions {
  /** Unsupported or unknown: renders `ctl-off`, which moots every other flag. */
  off?: boolean;
  /** This binding differs from the saved one. */
  dirty?: boolean;
  /** The bound provider has no credential (§9.6). */
  warning?: boolean;
  /** Per-control additions — `appearance-none`, `tabular-nums`, flex layout. */
  extra?: string;
}

/**
 * Compose one control's classes.
 *
 * Branch-selected rather than concatenated, because the mutually exclusive parts
 * set the SAME property: `text-[12px]` vs `text-[14px]`, `border-j-primary` vs
 * `border-b-secondary`. Those have equal CSS specificity, so which one won would
 * be decided by Tailwind's output order rather than by this file — the same trap
 * `provider-row.tsx` documents for its ghost-button colours.
 *
 * `warning` and `dirty` are the one pair that does NOT compete: the amber border
 * states *why the binding is broken*, the ring states *that you have changed it*.
 * Collapsing them would leave the founder rebinding a warned tier and getting no
 * changed-feedback on the single control they had just touched.
 */
export function ctl({ off, dirty, warning, extra }: CtlOptions = {}): string {
  if (off) return `${CTL_BASE} ${CTL_OFF}`;
  const border = warning ? BORDER_WARNING : dirty ? BORDER_DIRTY : BORDER_IDLE;
  const ring = dirty ? ` ${RING_DIRTY}` : "";
  return `${CTL_BASE} ${CTL_ENABLED} ${border}${ring}${extra ? ` ${extra}` : ""}`;
}

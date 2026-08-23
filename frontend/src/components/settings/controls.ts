/**
 * The §9.3 control primitives, in one place.
 *
 * These metrics were already triplicated across the settings surface before
 * anyone had written a fourth copy — so the next tweak to a control height meant
 * finding every literal. This module is the single definition; every settings
 * control and button imports it rather than restating it, and the last
 * hand-rolled copies (`providers/provider-credential-form.tsx`,
 * `providers/provider-row.tsx`, `providers/remove-confirmation.tsx`) were folded
 * in with the §9.10 mobile pass.
 *
 * The one deliberate exception is `providers/provider-filter.tsx`: a segment in
 * a segmented group is not a button in a row — it has no border of its own, it
 * carries its own selected fill, and its 28px `sm`+ height is smaller than any
 * size here. Making it a fifth `btn()` variant would widen this module to cover
 * one call site. It states §9.10's 44px floor itself.
 */

/**
 * **No focus styles here, and none in any caller.** `globals.css`'s
 * `:focus-visible { outline: 2px solid }` is emitted UNLAYERED while every
 * Tailwind utility is emitted inside `@layer utilities`, and an unlayered
 * normal declaration beats every layered one whatever its specificity. So a
 * `focus-visible:outline-none` on a control suppresses nothing, and a
 * `focus-visible:ring-*` beside it is a second indicator in a second colour.
 * The note over that rule carries the full reasoning.
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

// ── Buttons (§9.3) ─────────────────────────────────────────────────────────

/** Geometry and type shared by every settings button. Colour lives in the
 *  variant, for the reason `ctl` gives above: two colour utilities in one class
 *  attribute have equal specificity, so which one wins is decided by Tailwind's
 *  output order rather than by the call site. `provider-row.tsx` documents the
 *  same trap — appending `text-j-error` after `text-t-secondary` renders grey. */
const BTN_BASE =
  "inline-flex items-center justify-center shrink-0 text-[13px] " +
  "rounded-[var(--radius-md)] transition-colors cursor-pointer " +
  "disabled:opacity-45 disabled:cursor-default";

/** §9.3's two heights, and its mobile row. Padding-x differs by size AND by
 *  family (13/12 at `md`, 12/11 at `sm`, 18/16 below the `sm` breakpoint), so
 *  the two are selected together rather than composed from separate tables. */
const BTN_SIZE = {
  md: {
    primary: "h-[44px] sm:h-[32px] px-[18px] sm:px-[13px]",
    ghost: "h-[44px] sm:h-[32px] px-[16px] sm:px-[12px]",
  },
  sm: {
    primary: "h-[44px] sm:h-[30px] px-[18px] sm:px-[12px]",
    ghost: "h-[44px] sm:h-[30px] px-[16px] sm:px-[11px]",
  },
} as const;

/**
 * The full colour statement per variant — never a fragment to append to.
 *
 * Ghost is 400; only primary is 500 (§9.3). The existing hand-rolled copies all
 * carry `font-medium` on their ghosts; migrating them to this function is the
 * point, and it will correct that weight.
 *
 * A tinted ghost is a VARIANT, not a colour argument, so a caller cannot hand
 * in a class that loses the cascade to the one already there.
 */
const BTN_VARIANT = {
  primary:
    "font-medium border border-transparent bg-j-primary text-j-primary-fg " +
    "hover:bg-j-primary-hover",
  ghost:
    "font-normal bg-transparent border border-b-primary text-t-secondary " +
    "hover:bg-surface-2",
  /** `Remove` — error-coloured AT REST, not on hover: a hover-only danger colour
   *  sits inside `@media (hover: hover)` and never renders on a phone at all. */
  danger:
    "font-normal bg-transparent border border-b-primary text-j-error " +
    "hover:bg-surface-2",
  /** §9.6's `Connect {provider}` on a warned tier card. */
  warning:
    "font-normal bg-transparent border border-j-warning/40 text-j-warning " +
    "hover:bg-j-warning-soft",
} as const;

export interface BtnOptions {
  /** §9.3: `md` for card-level and save-bar actions, `sm` inside dense rows. */
  size?: keyof typeof BTN_SIZE;
  variant?: keyof typeof BTN_VARIANT;
  /** Per-button additions — layout, `w-full`, `tabular-nums`. Never colour. */
  extra?: string;
}

/** Compose one button's classes. The counterpart to `ctl()`, and for the same
 *  reason: these metrics were already restated in three files before a fourth
 *  copy could be written. */
export function btn({
  size = "md",
  variant = "ghost",
  extra,
}: BtnOptions = {}): string {
  const family = variant === "primary" ? "primary" : "ghost";
  const geometry = BTN_SIZE[size][family];
  return `${BTN_BASE} ${geometry} ${BTN_VARIANT[variant]}${extra ? ` ${extra}` : ""}`;
}

"use client";

/** The three views of the provider list. Exported so the owning tab types its
 *  own state from the control rather than re-declaring the union. */
export type ProviderFilterValue = "all" | "connected" | "available";

const SEGMENTS: readonly { value: ProviderFilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "connected", label: "Connected" },
  { value: "available", label: "Available" },
];

/** 44px tall below the `sm` breakpoint so a segment stays a legal touch target. */
const SEGMENT_BASE =
  "h-[44px] sm:h-[28px] px-[12px] text-[12.5px] rounded-[6px] " +
  "transition-colors duration-150 cursor-pointer";

export interface ProviderFilterProps {
  value: ProviderFilterValue;
  onChange: (value: ProviderFilterValue) => void;
}

/**
 * The segmented All / Connected / Available control.
 *
 * Presentational: it owns no state and knows nothing about the provider list —
 * the parent tab holds the selection and does the filtering.
 *
 * Real `<button>`s carrying `aria-pressed`, not a `radiogroup`: a radiogroup
 * promises roving-focus arrow-key navigation that this control does not
 * implement, and a toggle-button group is the honest description of three
 * mutually exclusive filters that are each independently focusable by Tab.
 */
export function ProviderFilter({ value, onChange }: ProviderFilterProps) {
  return (
    <div
      role="group"
      aria-label="Filter providers"
      className="inline-flex items-center gap-[2px] p-[3px] bg-surface-2 border border-b-secondary rounded-[var(--radius-md)]"
    >
      {SEGMENTS.map((segment) => {
        const selected = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(segment.value)}
            className={`${SEGMENT_BASE} ${
              selected
                ? "bg-surface-4 text-t-primary font-medium"
                : "text-t-tertiary hover:text-t-secondary"
            }`}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}

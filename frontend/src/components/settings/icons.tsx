/**
 * The §9.11 icon table, in one place.
 *
 * Inline stroke SVG only — no icon library — matching the house style:
 * `fill="none"`, `stroke="currentColor"`, round caps and joins. Colour comes
 * from `currentColor`, so an icon is tinted by its container's text colour and
 * never carries a palette value of its own.
 *
 * The search glyph alone already existed in three places at three sizes, so each
 * icon takes a `size`: §9.11 fixes the geometry, call sites fix the scale (the
 * search glyph is 11px on a binding field, 13px in the providers toolbar, 15px
 * in the model picker).
 *
 * `settings-modal.tsx`, `settings-rail.tsx` and `tabs/providers-tab.tsx` still
 * carry their own copies; they are held by other implementers and migrate later.
 */

export interface IconProps {
  size?: number;
  className?: string;
}

interface GlyphProps extends IconProps {
  viewBox: string;
  strokeWidth: number;
  children: React.ReactNode;
}

function Glyph({ viewBox, strokeWidth, size, className, children }: GlyphProps) {
  return (
    <svg
      viewBox={viewBox}
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ? `shrink-0 ${className}` : "shrink-0"}
    >
      {children}
    </svg>
  );
}

/** Opens a command palette, never a dropdown — the affordance has to say which. */
export function SearchIcon({ size = 12, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 12 12" strokeWidth={1.4} size={size} className={className}>
      <circle cx="5.2" cy="5.2" r="3.3" />
      <path d="M7.7 7.7l2.1 2.1" />
    </Glyph>
  );
}

/** The one glyph that DOES mean a native select. */
export function ChevronDownIcon({ size = 10, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 10 10" strokeWidth={1.3} size={size} className={className}>
      <path d="M2.5 4l2.5 2.5L7.5 4" />
    </Glyph>
  );
}

export function ChevronRightIcon({ size = 14, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 14 14" strokeWidth={1.5} size={size} className={className}>
      <path d="M5.5 3L9.5 7l-4 4" />
    </Glyph>
  );
}

export function ChevronLeftIcon({ size = 14, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 14 14" strokeWidth={1.7} size={size} className={className}>
      <path d="M8.5 3L4.5 7l4 4" />
    </Glyph>
  );
}

export function CheckIcon({ size = 14, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 14 14" strokeWidth={1.8} size={size} className={className}>
      <path d="M2.8 7.4l2.7 2.7 5.7-6" />
    </Glyph>
  );
}

export function WarningIcon({ size = 14, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 14 14" strokeWidth={1.5} size={size} className={className}>
      <circle cx="7" cy="7" r="5.6" />
      <path d="M7 4.5v3.2M7 9.9v.2" />
    </Glyph>
  );
}

export function LockIcon({ size = 14, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 14 14" strokeWidth={1.4} size={size} className={className}>
      <rect x="2.5" y="6" width="9" height="6" rx="1.5" />
      <path d="M4.75 6V4.25a2.25 2.25 0 014.5 0V6" />
    </Glyph>
  );
}

export function CloseIcon({ size = 16, className }: IconProps) {
  return (
    <Glyph viewBox="0 0 16 16" strokeWidth={1.5} size={size} className={className}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Glyph>
  );
}

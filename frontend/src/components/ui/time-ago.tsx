"use client";

/**
 * A stamp slightly ahead of the client clock is skew between two machines,
 * not a scheduled event, and must still read as "just now". Only a stamp
 * further ahead than any plausible skew describes something that has not
 * happened yet.
 */
const CLOCK_SKEW_TOLERANCE_SECONDS = 60;

/** Weekday, day, short month, hour and minute — localised, never hand-formatted. */
const SCHEDULED_FORMAT: Intl.DateTimeFormatOptions = {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
};

/**
 * A relative stamp is only honest about the past. A meeting three weeks out
 * is a date you need to read, not a distance you need to decode — and the
 * elapsed-seconds arithmetic below goes negative for it, which without the
 * future branch made every scheduled thing render as "just now".
 */
export function formatTimeAgo(dateStr: string): string {
  const then = new Date(dateStr);
  const seconds = Math.floor((Date.now() - then.getTime()) / 1000);

  if (seconds < -CLOCK_SKEW_TOLERANCE_SECONDS)
    return then.toLocaleString(undefined, SCHEDULED_FORMAT);

  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
}

export function TimeAgo({
  date,
  tone = "text-t-tertiary",
  className = "",
}: {
  date: string | null;
  /** Colour utility for the rendered `<time>` element. Exactly one colour
   *  class should ever reach the element — Tailwind resolves conflicting
   *  utilities by their order in the generated stylesheet, not by their
   *  order in `className`, so a caller cannot reliably override the
   *  hardcoded default via `className` alone. Defaults to the original
   *  tone so existing callers are unaffected. */
  tone?: string;
  className?: string;
}) {
  if (!date)
    return <span className={`text-t-muted ${className}`}>--</span>;
  return (
    <time
      dateTime={date}
      className={`${tone} ${className}`}
      title={new Date(date).toLocaleString()}
    >
      {formatTimeAgo(date)}
    </time>
  );
}

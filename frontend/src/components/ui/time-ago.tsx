"use client";

function formatTimeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const seconds = Math.floor((now - then) / 1000);

  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
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

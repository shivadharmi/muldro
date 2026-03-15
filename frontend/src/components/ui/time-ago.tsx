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

export function TimeAgo({ date, className = "" }: { date: string | null; className?: string }) {
  if (!date) return <span className={`text-neutral-600 ${className}`}>--</span>;
  return (
    <span className={`text-neutral-500 ${className}`} title={new Date(date).toLocaleString()}>
      {formatTimeAgo(date)}
    </span>
  );
}

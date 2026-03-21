"use client";

interface Props {
  sinceLastVisit: string;
  priorityCount: number;
  eventCount: number;
}

export function ChangesSinceAwayStrip({
  sinceLastVisit,
  priorityCount,
  eventCount,
}: Props) {
  return (
    <div className="flex items-center gap-4 px-4 py-2 bg-surface-1 rounded-[var(--radius-md)] text-sm">
      <span className="text-t-tertiary">Since {sinceLastVisit}</span>
      {priorityCount > 0 && (
        <span className="text-status-warning font-medium">
          {priorityCount} priority item{priorityCount > 1 ? "s" : ""}
        </span>
      )}
      {eventCount > 0 && (
        <span className="text-t-secondary">
          {eventCount} event{eventCount > 1 ? "s" : ""}
        </span>
      )}
    </div>
  );
}

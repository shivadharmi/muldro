"use client";

import type { MemoryItem } from "@/lib/types";

interface Props {
  memory: MemoryItem;
}

export function MemoryDetail({ memory }: Props) {
  return (
    <div className="p-4 space-y-3">
      <div>
        <span className="text-xs text-t-tertiary capitalize">{memory.memory_type}</span>
        {memory.scope && (
          <span className="text-xs text-t-tertiary ml-2">{memory.scope}</span>
        )}
      </div>

      <p className="text-sm text-t-primary">{memory.fact_text}</p>

      <div className="flex items-center gap-4 text-xs text-t-tertiary">
        <span>Confidence: {Math.round((memory.confidence ?? 1) * 100)}%</span>
        <span className="capitalize">Status: {memory.status ?? "active"}</span>
        {memory.access_count != null && (
          <span>Accessed: {memory.access_count}x</span>
        )}
      </div>

      {/* Confidence bar */}
      <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
        <div
          className="h-full bg-accent-primary rounded-full"
          style={{ width: `${(memory.confidence ?? 1) * 100}%` }}
        />
      </div>

      {memory.entity_ids && memory.entity_ids.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-1">
            Linked Entities
          </h4>
          <div className="flex flex-wrap gap-1">
            {memory.entity_ids.map((id) => (
              <span
                key={id}
                className="px-2 py-0.5 rounded bg-surface-1 text-xs text-t-tertiary font-mono"
              >
                {id.slice(0, 16)}
              </span>
            ))}
          </div>
        </div>
      )}

      {memory.is_stale && (
        <div className="border-l-4 border-status-warning bg-status-warning/5 p-2 rounded-r-[var(--radius-sm)]">
          <p className="text-xs text-status-warning">
            This memory may be stale and needs review.
          </p>
        </div>
      )}
    </div>
  );
}

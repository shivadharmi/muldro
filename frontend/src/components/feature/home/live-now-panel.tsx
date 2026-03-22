"use client";

import { LiveActivityFeed } from "@/components/primitives/live-activity-feed";
import { useActivityStore } from "@/stores/activity-store";

export function LiveNowPanel() {
  const eventCount = useActivityStore((s) => s.events.length);
  const sseConnected = useActivityStore((s) => s.sseConnected);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Live Activity
        </h3>
        <div className="flex items-center gap-2">
          {eventCount > 0 && (
            <span className="text-[10px] text-t-tertiary">{eventCount} event{eventCount !== 1 ? "s" : ""}</span>
          )}
          {sseConnected && (
            <span className="flex items-center gap-1 text-[10px] text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              live
            </span>
          )}
        </div>
      </div>
      <div className="max-h-[280px] overflow-y-auto -mx-1 px-1">
        <LiveActivityFeed />
      </div>
    </div>
  );
}

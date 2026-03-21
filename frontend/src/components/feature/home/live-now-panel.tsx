"use client";

import { LiveActivityFeed } from "@/components/primitives/live-activity-feed";

export function LiveNowPanel() {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
        Live Activity
      </h3>
      <LiveActivityFeed />
    </div>
  );
}

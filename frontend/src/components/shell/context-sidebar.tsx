"use client";

import { useShellStore } from "@/stores/shell-store";
import { EvidencePanel } from "@/components/primitives/evidence-panel";
import { LiveActivityFeed } from "@/components/primitives/live-activity-feed";

const TABS = ["context", "evidence", "activity"] as const;

export function ContextSidebar() {
  const { rightSidebarOpen, rightSidebarTab, setRightSidebarTab } =
    useShellStore();

  if (!rightSidebarOpen) return null;

  return (
    <aside className="w-80 border-l border-b-primary bg-surface-0 flex flex-col h-full overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-b-primary">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setRightSidebarTab(tab)}
            className={`flex-1 py-2 text-xs font-medium capitalize transition-colors cursor-pointer ${
              rightSidebarTab === tab
                ? "text-accent-primary border-b-2 border-accent-primary"
                : "text-t-tertiary hover:text-t-secondary"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3">
        {rightSidebarTab === "context" && (
          <div className="text-t-tertiary text-sm">
            <p className="mb-2 font-medium text-t-secondary">Context</p>
            <p>Select a message to see related entities, memories, and sources.</p>
          </div>
        )}
        {rightSidebarTab === "evidence" && <EvidencePanel />}
        {rightSidebarTab === "activity" && <LiveActivityFeed />}
      </div>
    </aside>
  );
}

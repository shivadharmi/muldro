"use client";

import { useEffect, useReducer } from "react";
import { useShellStore } from "@/stores/shell-store";
import { useCommandStore } from "@/stores/command-store";
import { EvidencePanel } from "@/components/primitives/evidence-panel";
import { LiveActivityFeed } from "@/components/primitives/live-activity-feed";
import { fetchMessageContext } from "@/lib/api";
import type { ContextSidebarData } from "@/lib/types/context";

const TABS = ["context", "evidence", "activity"] as const;

type ContextAction =
  | { type: "loading" }
  | { type: "loaded"; data: ContextSidebarData | null }
  | { type: "error" };

interface ContextState {
  loading: boolean;
  data: ContextSidebarData | null;
}

function contextReducer(_state: ContextState, action: ContextAction): ContextState {
  switch (action.type) {
    case "loading":
      return { loading: true, data: null };
    case "loaded":
      return { loading: false, data: action.data };
    case "error":
      return { loading: false, data: null };
  }
}

function formatRecency(timestamp: string | null): string | null {
  if (!timestamp) return null;
  const then = new Date(timestamp).getTime();
  if (Number.isNaN(then)) return null;
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "now";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function buildRollup(context: ContextSidebarData): string | null {
  const { evidence } = context;
  const parts: string[] = [];

  const recency = formatRecency(context.timestamp);
  if (recency) parts.push(`recency ${recency}`);

  if (typeof evidence.confidence === "number") {
    parts.push(`confidence ${evidence.confidence.toFixed(2)}`);
  }

  const sourceTypes = Array.from(new Set(evidence.sources.map((s) => s.source_type)));
  if (sourceTypes.length > 0) {
    parts.push(`sources ${sourceTypes.join(" · ")}`);
  }

  return parts.length > 0 ? parts.join(" · ") : null;
}

function ContextTab() {
  const focusedMessageId = useCommandStore((s) => s.focusedMessageId);
  const [state, dispatch] = useReducer(contextReducer, { loading: false, data: null });

  useEffect(() => {
    if (!focusedMessageId) return;

    let cancelled = false;
    dispatch({ type: "loading" });

    fetchMessageContext(focusedMessageId)
      .then((data) => {
        if (!cancelled) dispatch({ type: "loaded", data });
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: "loaded", data: null });
      });

    return () => {
      cancelled = true;
    };
  }, [focusedMessageId]);

  const hasSelection = !!focusedMessageId;
  const { loading, data: context } = state;

  if (!hasSelection) {
    return (
      <div className="text-t-tertiary text-sm">
        <p className="mb-2 font-medium text-t-secondary">Context</p>
        <p>Select a message to see related entities, memories, and sources.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="text-t-tertiary text-sm animate-pulse">Loading context…</div>
    );
  }

  if (!context) {
    return (
      <div className="text-t-tertiary text-sm">
        <p className="mb-2 font-medium text-t-secondary">Context</p>
        <p>No context available for this message.</p>
      </div>
    );
  }

  const { evidence } = context;
  const rollup = buildRollup(context);

  return (
    <div className="space-y-4">
      {/* Compact recency · confidence · sources rollup */}
      {rollup && (
        <p className="text-[11px] text-t-muted font-mono">{rollup}</p>
      )}

      {/* Entities — chips */}
      {evidence.entities.length > 0 && (
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wide text-t-muted mb-2">
            Entities
          </p>
          <div className="flex flex-wrap gap-1.5">
            {evidence.entities.map((e) => (
              <span
                key={e.entity_id}
                title={`${e.entity_type} · ${Math.round(e.relevance * 100)}%`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-2 border border-b-secondary text-[11px] text-t-primary"
              >
                {e.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Memories */}
      {evidence.memories.length > 0 && (
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wide text-t-muted mb-2">Memories</p>
          <div className="space-y-1.5">
            {evidence.memories.map((m) => (
              <div key={m.memory_id} className="text-xs">
                <span className="text-t-tertiary text-[10px] uppercase">{m.memory_type}</span>
                <p className="text-t-secondary line-clamp-2 mt-0.5">{m.content}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sources */}
      {evidence.sources.length > 0 && (
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wide text-t-muted mb-2">Sources</p>
          <div className="space-y-1">
            {evidence.sources.map((s) => (
              <div key={s.source_id} className="flex items-center gap-2 text-xs">
                <span className="text-t-tertiary capitalize text-[10px]">{s.source_type}</span>
                <span className="text-t-secondary truncate">{s.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ContextSidebar() {
  const { rightSidebarOpen, rightSidebarTab, setRightSidebarTab } =
    useShellStore();

  if (!rightSidebarOpen) return null;

  return (
    <aside className="w-80 border-l border-b-secondary bg-surface-0 flex flex-col h-full overflow-hidden animate-slide-in-right">
      {/* Tab bar */}
      <div className="flex border-b border-b-secondary">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setRightSidebarTab(tab)}
            className={`flex-1 py-2.5 text-[11px] font-medium capitalize transition-colors cursor-pointer ${
              rightSidebarTab === tab
                ? "text-j-primary border-b-2 border-j-primary"
                : "text-t-muted hover:text-t-secondary"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-4">
        {rightSidebarTab === "context" && <ContextTab />}
        {rightSidebarTab === "evidence" && <EvidencePanel />}
        {rightSidebarTab === "activity" && <LiveActivityFeed />}
      </div>
    </aside>
  );
}

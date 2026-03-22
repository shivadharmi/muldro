"use client";

import { useEffect, useReducer } from "react";
import { useShellStore } from "@/stores/shell-store";
import { useCommandStore } from "@/stores/command-store";
import { EvidencePanel } from "@/components/primitives/evidence-panel";
import { LiveActivityFeed } from "@/components/primitives/live-activity-feed";

const TABS = ["context", "evidence", "activity"] as const;

interface ContextData {
  entities: { name: string; type: string; relevance: number }[];
  memories: { content: string; type: string }[];
  traceId: string | null;
  runId: string | null;
}

type ContextAction =
  | { type: "loading" }
  | { type: "loaded"; data: ContextData | null }
  | { type: "error" };

interface ContextState {
  loading: boolean;
  data: ContextData | null;
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

function ContextTab() {
  const focusedMessageId = useCommandStore((s) => s.focusedMessageId);
  const [state, dispatch] = useReducer(contextReducer, { loading: false, data: null });

  useEffect(() => {
    if (!focusedMessageId) return;

    let cancelled = false;
    dispatch({ type: "loading" });

    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "";
    fetch(`${backendUrl}/v1/conversations/messages/${focusedMessageId}/context`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) dispatch({ type: "loaded", data });
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: "error" });
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
      <div className="text-t-tertiary text-sm animate-pulse">Loading context...</div>
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

  return (
    <div className="space-y-4">
      {/* Trace/Run IDs */}
      {(context.traceId || context.runId) && (
        <div className="space-y-1">
          {context.traceId && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-t-tertiary">Trace</span>
              <code className="text-t-secondary font-mono truncate">{context.traceId}</code>
            </div>
          )}
          {context.runId && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-t-tertiary">Run</span>
              <code className="text-t-secondary font-mono truncate">{context.runId}</code>
            </div>
          )}
        </div>
      )}

      {/* Entities */}
      {context.entities.length > 0 && (
        <div>
          <p className="text-xs font-medium text-t-secondary mb-2">Entities</p>
          <div className="space-y-1">
            {context.entities.map((e, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="px-1.5 py-0.5 rounded bg-surface-2 text-t-tertiary text-[10px] uppercase">
                    {e.type}
                  </span>
                  <span className="text-t-primary">{e.name}</span>
                </div>
                <span className="text-t-tertiary">{Math.round(e.relevance * 100)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Memories */}
      {context.memories.length > 0 && (
        <div>
          <p className="text-xs font-medium text-t-secondary mb-2">Memories</p>
          <div className="space-y-1.5">
            {context.memories.map((m, i) => (
              <div key={i} className="text-xs">
                <span className="text-t-tertiary text-[10px] uppercase">{m.type}</span>
                <p className="text-t-secondary line-clamp-2 mt-0.5">{m.content}</p>
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
        {rightSidebarTab === "context" && <ContextTab />}
        {rightSidebarTab === "evidence" && <EvidencePanel />}
        {rightSidebarTab === "activity" && <LiveActivityFeed />}
      </div>
    </aside>
  );
}

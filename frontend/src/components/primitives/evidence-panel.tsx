"use client";

import { useEffect, useReducer } from "react";
import { useCommandStore } from "@/stores/command-store";
import { fetchMessageEvidence } from "@/lib/api";
import type { EvidenceBundle } from "@/lib/types/context";

type State = { loading: boolean; evidence: EvidenceBundle | null };
type Action =
  | { type: "loading" }
  | { type: "loaded"; evidence: EvidenceBundle | null }
  | { type: "error" };

function reducer(_state: State, action: Action): State {
  switch (action.type) {
    case "loading":
      return { loading: true, evidence: null };
    case "loaded":
      return { loading: false, evidence: action.evidence };
    case "error":
      return { loading: false, evidence: null };
  }
}

export function EvidencePanel() {
  const focusedMessageId = useCommandStore((s) => s.focusedMessageId);
  const [state, dispatch] = useReducer(reducer, { loading: false, evidence: null });

  useEffect(() => {
    if (!focusedMessageId) return;

    let cancelled = false;
    dispatch({ type: "loading" });

    fetchMessageEvidence(focusedMessageId)
      .then((data) => {
        if (!cancelled) dispatch({ type: "loaded", evidence: data });
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: "loaded", evidence: null });
      });

    return () => {
      cancelled = true;
    };
  }, [focusedMessageId]);

  const { loading, evidence } = state;

  if (!focusedMessageId) {
    return (
      <div className="text-sm text-t-tertiary">
        Select a message to see evidence and sources.
      </div>
    );
  }

  if (loading) {
    return <div className="text-sm text-t-tertiary animate-pulse">Loading evidence...</div>;
  }

  if (!evidence) {
    return (
      <div className="text-sm text-t-tertiary">
        No evidence available for this message.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Entities */}
      {evidence.entities.length > 0 && (
        <section aria-label="Entities">
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            Entities
          </h4>
          <ul className="space-y-1.5">
            {evidence.entities.map((e) => (
              <li
                key={e.entity_id}
                className="flex items-center gap-2 text-sm text-t-secondary"
              >
                <span className="w-5 h-5 rounded bg-surface-2 flex items-center justify-center text-xs text-t-tertiary">
                  {e.entity_type[0]?.toUpperCase()}
                </span>
                <span className="truncate">{e.name}</span>
                <span className="ml-auto text-xs text-t-tertiary">
                  {Math.round(e.relevance * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Memories */}
      {evidence.memories.length > 0 && (
        <section aria-label="Memories">
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            Memories
          </h4>
          <ul className="space-y-1.5">
            {evidence.memories.map((m) => (
              <li key={m.memory_id} className="text-sm text-t-secondary">
                <span className="text-xs text-t-tertiary">[{m.memory_type}]</span>{" "}
                <span className="line-clamp-2">{m.content}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Sources */}
      {evidence.sources.length > 0 && (
        <section aria-label="Sources">
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            Sources
          </h4>
          <ul className="space-y-1">
            {evidence.sources.map((s, i) => (
              <li
                key={`${s.source_id}-${i}`}
                className="flex items-center gap-2 text-sm text-t-secondary"
              >
                <span className="text-xs text-t-tertiary capitalize">
                  {s.source_type}
                </span>
                <span className="truncate">{s.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Route info */}
      {evidence.route_info && (
        <section aria-label="Route">
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-2">
            Route
          </h4>
          <pre className="text-xs text-t-tertiary bg-surface-1 p-2 rounded-[var(--radius-sm)] overflow-x-auto">
            {JSON.stringify(evidence.route_info, null, 2)}
          </pre>
        </section>
      )}

      {/* Confidence / Risk */}
      <div className="flex items-center gap-4 text-xs text-t-tertiary">
        {evidence.confidence != null && (
          <span>Confidence: {Math.round(evidence.confidence * 100)}%</span>
        )}
        {evidence.risk_level && (
          <span className="capitalize">Risk: {evidence.risk_level}</span>
        )}
      </div>
    </div>
  );
}

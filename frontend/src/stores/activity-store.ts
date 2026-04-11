/** Activity state: live system events, unread count, recent feed.
 *
 * On first access (or explicit refresh), fetches from /v1/runtime/activity.
 * Subsequent real-time events arrive via SSE subscription and are prepended.
 */

import { create } from "zustand";
import { getStoredToken } from "@/lib/auth";

import type { RuntimeEvent } from "@/lib/types/runtime";

interface ActivityState {
  events: RuntimeEvent[];
  unreadCount: number;
  initialized: boolean;
  sseConnected: boolean;

  addEvent: (event: RuntimeEvent) => void;
  addEvents: (events: RuntimeEvent[]) => void;
  markAllRead: () => void;
  clearEvents: () => void;
  /** Fetch initial events from the backend. Safe to call multiple times. */
  initialize: () => Promise<void>;
  /** Subscribe to SSE runtime events stream. Returns cleanup function. */
  subscribeSSE: () => () => void;
}

const MAX_EVENTS = 200;

export const useActivityStore = create<ActivityState>((set, get) => ({
  events: [],
  unreadCount: 0,
  initialized: false,
  sseConnected: false,

  addEvent: (event) =>
    set((s) => ({
      events: [event, ...s.events].slice(0, MAX_EVENTS),
      unreadCount: s.unreadCount + 1,
    })),

  addEvents: (events) =>
    set((s) => ({
      events: [...events, ...s.events].slice(0, MAX_EVENTS),
      unreadCount: s.unreadCount + events.length,
    })),

  markAllRead: () => set({ unreadCount: 0 }),

  clearEvents: () => set({ events: [], unreadCount: 0 }),

  initialize: async () => {
    if (get().initialized) return;
    // Events arrive via SSE — no initial fetch needed
    set({ initialized: true });
  },

  subscribeSSE: () => {
    if (get().sseConnected) return () => {};

    // EventSource can't send Authorization headers, so pass the
    // session token as a query param. Route through Next.js proxy.
    const token = getStoredToken();
    const url = token
      ? `/api/realtime/runtime?token=${encodeURIComponent(token)}`
      : "/api/realtime/runtime";

    let eventSource: EventSource;
    try {
      eventSource = new EventSource(url, { withCredentials: true });
    } catch {
      return () => {};
    }

    const handleEvent = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const event: RuntimeEvent = {
          event_id: data.event_id || `sse_${Date.now()}`,
          run_id: data.run_id || null,
          step_id: data.step_id || null,
          event_type: e.type || data.event_type || "unknown",
          occurred_at: data.occurred_at || new Date().toISOString(),
          payload: data,
        };
        get().addEvent(event);
      } catch {
        // Skip malformed events
      }
    };

    // Listen for all runtime event types
    const runtimeTypes = [
      "command_received", "plan_created", "step_routed", "run_created",
      "step_started", "step_completed", "step_failed",
      "approval_requested", "approval_resolved",
      "tool_call_started", "tool_call_completed", "tool_call_failed",
      "artifact_created", "surface_created",
      "agent_started", "agent_completed",
      "run_completed", "run_failed", "run_cancelled",
      "auto_execute_notify",
    ];

    for (const type of runtimeTypes) {
      eventSource.addEventListener(type, handleEvent);
    }

    eventSource.onopen = () => set({ sseConnected: true });
    eventSource.onerror = () => set({ sseConnected: false });

    set({ sseConnected: true });

    return () => {
      eventSource.close();
      set({ sseConnected: false });
    };
  },
}));

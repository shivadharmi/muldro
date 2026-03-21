/** Activity state: live system events, unread count, recent feed.
 *
 * On first access (or explicit refresh), fetches from /v1/runtime/activity.
 * Subsequent real-time events arrive via SSE/WebSocket and are prepended.
 */

import { create } from "zustand";

import type { RuntimeEvent } from "@/lib/types/runtime";
import { fetchRuntimeActivity } from "@/lib/api";

interface ActivityState {
  events: RuntimeEvent[];
  unreadCount: number;
  initialized: boolean;

  addEvent: (event: RuntimeEvent) => void;
  addEvents: (events: RuntimeEvent[]) => void;
  markAllRead: () => void;
  clearEvents: () => void;
  /** Fetch initial events from the backend. Safe to call multiple times. */
  initialize: () => Promise<void>;
}

const MAX_EVENTS = 200;

export const useActivityStore = create<ActivityState>((set, get) => ({
  events: [],
  unreadCount: 0,
  initialized: false,

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
    try {
      const events = await fetchRuntimeActivity(undefined, 50);
      set({ events, initialized: true });
    } catch {
      // API may not be available — silently degrade
      set({ initialized: true });
    }
  },
}));

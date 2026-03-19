/** SSE hook — subscribes to /v1/realtime/events for live updates. */

"use client";

import { useEffect, useRef } from "react";
import { getStoredToken } from "@/lib/auth";

export interface SSEEvent {
  event_type: string;
  data: Record<string, unknown>;
}

/**
 * Subscribe to the Jarvis realtime SSE stream.
 * Returns nothing — fires onEvent callback for each event.
 */
export function useSSE(
  onEvent: (event: SSEEvent) => void,
  enabled = true
) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (!enabled) return;

    const token = getStoredToken() || process.env.NEXT_PUBLIC_API_TOKEN || "";
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    const eventSource = new EventSource(`/api/realtime/events${qs}`);

    eventSource.onmessage = (msg) => {
      try {
        const parsed: SSEEvent = JSON.parse(msg.data);
        onEventRef.current(parsed);
      } catch {
        // skip malformed JSON
      }
    };

    eventSource.onerror = () => {
      // EventSource auto-reconnects; nothing to do
    };

    return () => {
      eventSource.close();
    };
  }, [enabled]);
}

/**
 * Hook for subscribing to a specific run's progress stream.
 */
export function useRunSSE(
  runId: string | null,
  onEvent: (event: SSEEvent) => void
) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (!runId) return;

    const eventSource = new EventSource(
      `/api/realtime/runs/${runId}`
    );

    eventSource.onmessage = (msg) => {
      try {
        const parsed: SSEEvent = JSON.parse(msg.data);
        onEventRef.current(parsed);
      } catch {
        // skip malformed
      }
    };

    return () => {
      eventSource.close();
    };
  }, [runId]);
}

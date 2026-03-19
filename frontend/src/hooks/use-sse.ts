/** SSE hook — subscribes to /v1/realtime/events using fetch with Authorization header. */

"use client";

import { useEffect, useRef } from "react";
import { getStoredToken } from "@/lib/auth";

export interface SSEEvent {
  event_type: string;
  data: Record<string, unknown>;
}

/** Shared SSE reader using fetch (sends Authorization header, no query param token). */
function connectSSE(
  url: string,
  onEvent: (event: SSEEvent) => void,
  signal: AbortSignal
): void {
  const token = getStoredToken() || process.env.NEXT_PUBLIC_API_TOKEN || "";
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  fetch(url, { headers, signal })
    .then(async (res) => {
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch {
              // skip malformed JSON
            }
          }
        }
      }
    })
    .catch(() => {
      // aborted or network error — caller handles reconnect
    });
}

export function useSSE(onEvent: (event: SSEEvent) => void, enabled = true) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    connectSSE(
      "/api/realtime/events",
      (evt) => onEventRef.current(evt),
      controller.signal
    );

    return () => controller.abort();
  }, [enabled]);
}

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

    const controller = new AbortController();
    connectSSE(
      `/api/realtime/runs/${runId}`,
      (evt) => onEventRef.current(evt),
      controller.signal
    );

    return () => controller.abort();
  }, [runId]);
}

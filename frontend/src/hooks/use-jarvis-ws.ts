"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { A2UISurface, JarvisMessage } from "@/lib/a2ui-types";
import { getStoredToken } from "@/lib/auth";

function getWsUrl(userId: string): string {
  // No token in URL — auth via message after connect
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return `${process.env.NEXT_PUBLIC_WS_URL}/ws/${userId}`;
  }
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    `${window.location.protocol}//${window.location.host}`;
  const wsBase = new URL(backendUrl.replace(/^http/, "ws"));
  return `${wsBase.protocol}//${wsBase.host}/ws/${userId}`;
}

interface UseJarvisWsOptions {
  userId: string;
  onSurface?: (surface: A2UISurface) => void;
  onSurfaceUpdate?: (surfaceId: string, surface: A2UISurface) => void;
  onNotification?: (msg: JarvisMessage) => void;
  enabled?: boolean;
}

export function useJarvisWs({
  userId,
  onSurface,
  onSurfaceUpdate,
  onNotification,
  enabled = true,
}: UseJarvisWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Store callbacks in refs so the WebSocket effect doesn't re-run when they change
  const onSurfaceRef = useRef(onSurface);
  const onSurfaceUpdateRef = useRef(onSurfaceUpdate);
  const onNotificationRef = useRef(onNotification);
  useEffect(() => {
    onSurfaceRef.current = onSurface;
  }, [onSurface]);
  useEffect(() => {
    onSurfaceUpdateRef.current = onSurfaceUpdate;
  }, [onSurfaceUpdate]);
  useEffect(() => {
    onNotificationRef.current = onNotification;
  }, [onNotification]);

  useEffect(() => {
    if (!enabled) return;

    // Prevent duplicate connections
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    let intentionallyClosed = false;

    const connect = () => {
      if (intentionallyClosed) return;

      const ws = new WebSocket(getWsUrl(userId));
      wsRef.current = ws;

      ws.onopen = () => {
        // Send auth message immediately after connect
        const token = getStoredToken();
        if (token) {
          ws.send(JSON.stringify({ type: "auth", token }));
        } else {
          // No token — close immediately
          ws.close();
        }
      };

      ws.onmessage = (event) => {
        let msg: JarvisMessage;
        try {
          msg = JSON.parse(event.data) as JarvisMessage;
        } catch {
          console.warn(
            "[jarvis-ws] Malformed JSON:",
            typeof event.data === "string" ? event.data.slice(0, 200) : "non-string"
          );
          return;
        }

        if (!msg || typeof msg !== "object" || !("type" in msg)) {
          console.warn("[jarvis-ws] Invalid message structure");
          return;
        }

        if (msg.type === "auth_ok") {
          setConnected(true);
        } else if (msg.type === "auth_error") {
          ws.close();
        } else if (msg.type === "surface" && onSurfaceRef.current) {
          // Validate surface payload before dispatching
          if (!msg.surface?.id || !Array.isArray(msg.surface?.children)) {
            console.warn("[jarvis-ws] Invalid surface payload:", msg.surface?.id);
            return;
          }
          onSurfaceRef.current(msg.surface);
        } else if (msg.type === "surface_update" && onSurfaceUpdateRef.current) {
          onSurfaceUpdateRef.current(msg.surface_id, msg.payload);
        } else if (msg.type === "heartbeat") {
          // no-op
        } else if (onNotificationRef.current) {
          onNotificationRef.current(msg);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!intentionallyClosed) {
          reconnectTimer.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      intentionallyClosed = true;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [userId, enabled]);

  const sendAction = useCallback(
    (action: string, payload: Record<string, unknown>) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({ type: "action", payload: { action, ...payload } })
        );
      }
    },
    []
  );

  return { connected, sendAction };
}

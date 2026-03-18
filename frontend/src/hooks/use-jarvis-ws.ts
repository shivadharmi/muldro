"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { A2UISurface, JarvisMessage } from "@/lib/a2ui-types";
import { getStoredToken } from "@/lib/auth";

function getWsUrl(userId: string): string {
  // Use explicit env var if set (e.g. for local dev pointing at a different backend)
  if (process.env.NEXT_PUBLIC_WS_URL) {
    const base = process.env.NEXT_PUBLIC_WS_URL;
    const token = getStoredToken();
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${base}/ws/${userId}${qs}`;
  }
  // Derive from current page origin so it works in any deployment
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || `${window.location.protocol}//${window.location.host}`;
  const wsBase = backendUrl.replace(/^http/, "ws");
  const token = getStoredToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${new URL(wsBase).host}/ws/${userId}${qs}`;
}

interface UseJarvisWsOptions {
  userId: string;
  onSurface?: (surface: A2UISurface) => void;
  onNotification?: (msg: JarvisMessage) => void;
}

export function useJarvisWs({ userId, onSurface, onNotification }: UseJarvisWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getWsUrl(userId));
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      console.log("[jarvis-ws] connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as JarvisMessage;
        if (msg.type === "surface" && onSurface) {
          onSurface(msg.surface);
        } else if (msg.type === "heartbeat") {
          // no-op
        } else if (onNotification) {
          onNotification(msg);
        }
      } catch {
        console.warn("[jarvis-ws] failed to parse message", event.data);
      }
    };

    ws.onclose = () => {
      setConnected(false);
      console.log("[jarvis-ws] disconnected, reconnecting in 3s...");
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [userId, onSurface, onNotification]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendAction = useCallback((action: string, payload: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "action", payload: { action, ...payload } }));
    }
  }, []);

  return { connected, sendAction };
}

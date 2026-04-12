"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ActionResult, JarvisMessage, SurfaceUpdate, WorkspaceSurfacePush } from "@/lib/a2ui-types";
import { getStoredToken } from "@/lib/auth";

function getWsUrl(userId: string): string {
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
  onSurfacePush?: (surface: WorkspaceSurfacePush) => void;
  onSurfaceUpdate?: (update: SurfaceUpdate) => void;
  onActionResult?: (result: ActionResult) => void;
  onNotification?: (msg: JarvisMessage) => void;
  enabled?: boolean;
}

export function useJarvisWs({
  userId,
  onSurfacePush,
  onSurfaceUpdate,
  onActionResult,
  onNotification,
  enabled = true,
}: UseJarvisWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const onSurfacePushRef = useRef(onSurfacePush);
  const onActionResultRef = useRef(onActionResult);
  const onSurfaceUpdateRef = useRef(onSurfaceUpdate);
  const onNotificationRef = useRef(onNotification);
  useEffect(() => {
    onSurfacePushRef.current = onSurfacePush;
  }, [onSurfacePush]);
  useEffect(() => {
    onSurfaceUpdateRef.current = onSurfaceUpdate;
  }, [onSurfaceUpdate]);
  useEffect(() => {
    onActionResultRef.current = onActionResult;
  }, [onActionResult]);
  useEffect(() => {
    onNotificationRef.current = onNotification;
  }, [onNotification]);

  useEffect(() => {
    if (!enabled) return;

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
        const token = getStoredToken();
        if (token) {
          ws.send(JSON.stringify({ type: "auth", token }));
        } else {
          ws.close();
        }
      };

      ws.onmessage = (event) => {
        let msg: JarvisMessage;
        try {
          msg = JSON.parse(event.data) as JarvisMessage;
        } catch {
          return;
        }

        if (!msg || typeof msg !== "object" || !("type" in msg)) return;

        if (msg.type === "auth_ok") {
          setConnected(true);
        } else if (msg.type === "auth_error") {
          ws.close();
        } else if (msg.type === "surface" && onSurfacePushRef.current) {
          if (msg.surface?.id) {
            onSurfacePushRef.current(msg.surface);
          }
        } else if (msg.type === "action_result" && onActionResultRef.current) {
          onActionResultRef.current({
            action: msg.action,
            status: (msg.status as "success" | "error") ?? "success",
            result: msg.result,
            error: msg.error,
          });
        } else if (msg.type === "surface_update" && onSurfaceUpdateRef.current) {
          onSurfaceUpdateRef.current({
            surface_id: msg.surface_id,
            phase: msg.phase,
            steps: msg.steps,
            current_step: msg.current_step,
            progress: msg.progress,
            approval: msg.approval,
            results: msg.results,
          });
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

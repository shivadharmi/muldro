"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ActionResult, JarvisMessage, SurfaceUpdate, WorkspaceSurfacePush } from "@/lib/a2ui-types";
import { parseWsError, type ParsedApiError } from "@/lib/api-error";
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
  /** Top-level WS error frame: { status:"error", code, message, correlation_id }. */
  onError?: (err: ParsedApiError) => void;
  enabled?: boolean;
}

export function useJarvisWs({
  userId,
  onSurfacePush,
  onSurfaceUpdate,
  onActionResult,
  onNotification,
  onError,
  enabled = true,
}: UseJarvisWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const onSurfacePushRef = useRef(onSurfacePush);
  const onActionResultRef = useRef(onActionResult);
  const onSurfaceUpdateRef = useRef(onSurfaceUpdate);
  const onNotificationRef = useRef(onNotification);
  const onErrorRef = useRef(onError);
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
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!enabled) return;

    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    let intentionallyClosed = false;
    // Auth-level rejection (bad/expired token, user mismatch) is terminal for
    // this identity — reconnecting would just replay the same failure every 3s
    // (a reconnect storm). Only transient closes should reconnect.
    let authRejected = false;

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

        if (!msg || typeof msg !== "object") return;

        // Standardized top-level error frame: { status:"error", code, message,
        // correlation_id } — has no `type`. Surface the safe message only.
        if (!("type" in msg) && "status" in msg && msg.status === "error") {
          onErrorRef.current?.(parseWsError(msg));
          return;
        }

        if (!("type" in msg)) return;

        if (msg.type === "auth_ok") {
          setConnected(true);
        } else if (msg.type === "auth_error") {
          // Terminal for this identity: stop reconnecting and surface it so the
          // UI can prompt a re-auth rather than silently looping.
          authRejected = true;
          onErrorRef.current?.({
            code: "auth_error",
            message: typeof msg.message === "string" ? msg.message : "Authentication failed",
            correlationId: null,
          });
          ws.close();
        } else if (msg.type === "surface" && onSurfacePushRef.current) {
          if (msg.surface?.id) {
            onSurfacePushRef.current(msg.surface);
          }
        } else if (msg.type === "action_result" && onActionResultRef.current) {
          const status = (msg.status as "success" | "error") ?? "success";
          const parsed = status === "error" ? parseWsError(msg) : null;
          onActionResultRef.current({
            action: msg.action,
            status,
            result: msg.result,
            message: parsed?.message,
            code: parsed?.code,
            correlationId: parsed?.correlationId,
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
        if (!intentionallyClosed && !authRejected) {
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

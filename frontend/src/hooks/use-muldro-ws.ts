"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkspaceSurfacePush } from "@/lib/a2ui-types";
import type { ActionResult, MuldroMessage, SurfaceUpdate } from "@/lib/types/execution";
import { parseWsError, type ParsedApiError } from "@/lib/api-error";
import { getStoredToken } from "@/lib/auth";
import type { Unit } from "@/lib/types/unit";

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

interface UseMuldroWsOptions {
  userId: string;
  /** A live Unit from the view layer. See backend src/view/publish.py. */
  onUnitPush?: (unit: Unit) => void;
  onSurfacePush?: (surface: WorkspaceSurfacePush) => void;
  onSurfaceUpdate?: (update: SurfaceUpdate) => void;
  onActionResult?: (result: ActionResult) => void;
  onNotification?: (msg: MuldroMessage) => void;
  /** Top-level WS error frame: { status:"error", code, message, correlation_id }. */
  onError?: (err: ParsedApiError) => void;
  enabled?: boolean;
}

/**
 * Handlers the dispatcher may call. Every field optional — a page wires only
 * what it renders.
 */
export interface MuldroHandlers {
  onUnitPush?: (unit: Unit) => void;
  onSurfacePush?: (surface: WorkspaceSurfacePush) => void;
  onSurfaceUpdate?: (update: SurfaceUpdate) => void;
  onActionResult?: (result: ActionResult) => void;
  onNotification?: (msg: MuldroMessage) => void;
  onError?: (err: ParsedApiError) => void;
  onAuthOk?: () => void;
  onAuthError?: (err: ParsedApiError) => void;
}

/**
 * Route one parsed WS frame. Pure; no socket, no refs — which is what makes
 * the identity guards testable rather than eyeballed.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function dispatchMuldroMessage(msg: any, h: MuldroHandlers): void {
  if (!msg || typeof msg !== "object") return;

  // Standardized top-level error frame: { status:"error", code, message,
  // correlation_id } — has no `type`. Surface the safe message only.
  if (!("type" in msg) && "status" in msg && msg.status === "error") {
    h.onError?.(parseWsError(msg));
    return;
  }
  if (!("type" in msg)) return;

  if (msg.type === "auth_ok") {
    h.onAuthOk?.();
  } else if (msg.type === "auth_error") {
    h.onAuthError?.({
      code: "auth_error",
      message: typeof msg.message === "string" ? msg.message : "Authentication failed",
      correlationId: null,
    });
  } else if (msg.type === "unit" && h.onUnitPush) {
    // Guard on identity before dispatching. render_surface emitted
    // `surface_id` where the old branch read `id` and was dropped in silence
    // for months (spec §1); the guard stays and the publisher states the field.
    if (msg.key && msg.unit?.frame?.key) {
      h.onUnitPush(msg.unit as Unit);
    }
  } else if (msg.type === "surface" && h.onSurfacePush) {
    if (msg.surface?.id) {
      h.onSurfacePush(msg.surface);
    }
  } else if (msg.type === "action_result" && h.onActionResult) {
    const status = (msg.status as "success" | "error") ?? "success";
    const parsed = status === "error" ? parseWsError(msg) : null;
    h.onActionResult({
      action: msg.action,
      status,
      result: msg.result,
      message: parsed?.message,
      code: parsed?.code,
      correlationId: parsed?.correlationId,
    });
  } else if (msg.type === "surface_update" && h.onSurfaceUpdate) {
    // KEPT. app/history/page.tsx consumes this for live run rows; it is not
    // on spec §11's delete list and must survive the A2UI cutover.
    h.onSurfaceUpdate({
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
  } else {
    h.onNotification?.(msg);
  }
}

export function useMuldroWs({
  userId,
  onUnitPush,
  onSurfacePush,
  onSurfaceUpdate,
  onActionResult,
  onNotification,
  onError,
  enabled = true,
}: UseMuldroWsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const onUnitPushRef = useRef(onUnitPush);
  const onSurfacePushRef = useRef(onSurfacePush);
  const onActionResultRef = useRef(onActionResult);
  const onSurfaceUpdateRef = useRef(onSurfaceUpdate);
  const onNotificationRef = useRef(onNotification);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onUnitPushRef.current = onUnitPush;
  }, [onUnitPush]);
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
        let msg: MuldroMessage;
        try {
          msg = JSON.parse(event.data) as MuldroMessage;
        } catch {
          return;
        }

        dispatchMuldroMessage(msg, {
          onUnitPush: onUnitPushRef.current,
          onSurfacePush: onSurfacePushRef.current,
          onSurfaceUpdate: onSurfaceUpdateRef.current,
          onActionResult: onActionResultRef.current,
          onNotification: onNotificationRef.current,
          onError: onErrorRef.current,
          onAuthOk: () => setConnected(true),
          onAuthError: (err) => {
            // Terminal for this identity: stop reconnecting and surface it so
            // the UI can prompt a re-auth rather than silently looping.
            authRejected = true;
            onErrorRef.current?.(err);
            ws.close();
          },
        });
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

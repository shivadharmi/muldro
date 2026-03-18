/** Notifications hook — fetches + listens via SSE for realtime updates. */

"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchNotifications,
  markNotificationRead,
  dismissNotification,
} from "@/lib/api";
import { useSSE } from "@/hooks/use-sse";
import type { Notification } from "@/lib/types";

export function useNotifications() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchNotifications(undefined, 50);
      setNotifications(Array.isArray(data) ? data : []);
    } catch {
      // silently fail on fetch errors
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Listen for realtime notification events via SSE
  useSSE(
    useCallback(
      (event) => {
        if (
          event.event_type === "notification" ||
          event.event_type === "notification.sent"
        ) {
          const data = event.data as Partial<Notification>;
          if (data.notification_id) {
            setNotifications((prev) => {
              // Avoid duplicates
              if (prev.some((n) => n.notification_id === data.notification_id)) {
                return prev;
              }
              return [
                {
                  notification_id: data.notification_id!,
                  channel: data.channel || "web",
                  title: data.title || "",
                  body: data.body || null,
                  status: data.status || "pending",
                  priority_score: data.priority_score || 0.5,
                  sent_at: data.sent_at || null,
                  read_at: data.read_at || null,
                  created_at: data.created_at || new Date().toISOString(),
                } as Notification,
                ...prev,
              ];
            });
          }
        }
      },
      []
    )
  );

  const markRead = useCallback(async (id: string) => {
    await markNotificationRead(id);
    setNotifications((prev) =>
      prev.map((n) =>
        n.notification_id === id ? { ...n, status: "read" } : n
      )
    );
  }, []);

  const dismiss = useCallback(async (id: string) => {
    await dismissNotification(id);
    setNotifications((prev) =>
      prev.filter((n) => n.notification_id !== id)
    );
  }, []);

  const unreadCount = notifications.filter(
    (n) => n.status === "sent" || n.status === "pending"
  ).length;

  return {
    notifications,
    loading,
    unreadCount,
    refresh,
    markRead,
    dismiss,
  };
}

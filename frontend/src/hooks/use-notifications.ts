/** Notifications hook — fetches and manages notification state. */

"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchNotifications,
  markNotificationRead,
  dismissNotification,
} from "@/lib/api";
import type { Notification } from "@/lib/types";

export function useNotifications(pollIntervalMs = 30_000) {
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

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(interval);
  }, [refresh, pollIntervalMs]);

  const markRead = useCallback(
    async (id: string) => {
      await markNotificationRead(id);
      setNotifications((prev) =>
        prev.map((n) =>
          n.notification_id === id ? { ...n, status: "read" } : n
        )
      );
    },
    []
  );

  const dismiss = useCallback(
    async (id: string) => {
      await dismissNotification(id);
      setNotifications((prev) =>
        prev.filter((n) => n.notification_id !== id)
      );
    },
    []
  );

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

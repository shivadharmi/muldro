"use client";

import { PageHeader } from "@/components/layout/page-header";
import { NotificationCenter } from "@/components/notifications/notification-center";
import { useNotifications } from "@/hooks/use-notifications";

export default function NotificationsPage() {
  const { notifications, loading, unreadCount, markRead, dismiss } = useNotifications();

  return (
    <div className="p-6">
      <PageHeader
        title="Notifications"
        subtitle={`${unreadCount} unread notification${unreadCount !== 1 ? "s" : ""}`}
      />

      {loading ? (
        <p className="text-neutral-500 text-sm">Loading...</p>
      ) : (
        <NotificationCenter
          notifications={notifications}
          onMarkRead={markRead}
          onDismiss={dismiss}
        />
      )}
    </div>
  );
}

"use client";

import type { Notification } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, priorityVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { EmptyState } from "@/components/ui/empty-state";

function priorityLabel(score: number): string {
  if (score >= 80) return "critical";
  if (score >= 60) return "high";
  if (score >= 40) return "medium";
  return "low";
}

function NotificationItem({
  notification,
  onMarkRead,
  onDismiss,
}: {
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const isUnread = notification.status === "sent" || notification.status === "pending";
  const pLabel = priorityLabel(notification.priority_score);

  return (
    <Card className={isUnread ? "border-blue-800/50" : ""}>
      <CardBody>
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              {isUnread && (
                <span className="w-2 h-2 bg-blue-500 rounded-full flex-shrink-0" />
              )}
              <p className={`text-sm ${isUnread ? "font-medium" : "text-neutral-400"}`}>
                {notification.title}
              </p>
              <Badge variant={priorityVariant(pLabel)}>{pLabel}</Badge>
              <Badge variant="default">{notification.channel}</Badge>
            </div>
            {notification.body && (
              <p className="text-xs text-neutral-500 mt-1 ml-4">{notification.body}</p>
            )}
          </div>
          <div className="flex items-center gap-2 ml-3 flex-shrink-0">
            {isUnread && (
              <button
                onClick={() => onMarkRead(notification.notification_id)}
                className="text-neutral-500 hover:text-neutral-300 text-xs transition-colors"
              >
                Mark read
              </button>
            )}
            <button
              onClick={() => onDismiss(notification.notification_id)}
              className="text-neutral-500 hover:text-red-400 text-xs transition-colors"
            >
              Dismiss
            </button>
          </div>
        </div>
        <div className="mt-2 ml-4">
          <TimeAgo date={notification.created_at} className="text-xs" />
        </div>
      </CardBody>
    </Card>
  );
}

export function NotificationCenter({
  notifications,
  onMarkRead,
  onDismiss,
}: {
  notifications: Notification[];
  onMarkRead: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  if (notifications.length === 0) {
    return <EmptyState title="No notifications" description="You're all caught up" />;
  }

  const sorted = [...notifications].sort((a, b) => b.priority_score - a.priority_score);

  return (
    <div className="space-y-3">
      {sorted.map((n) => (
        <NotificationItem
          key={n.notification_id}
          notification={n}
          onMarkRead={onMarkRead}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}

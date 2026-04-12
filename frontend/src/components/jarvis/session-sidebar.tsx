"use client";

import { useEffect, useState } from "react";
import {
  fetchConversations,
  deleteConversation,
  type ConversationSummary,
} from "@/lib/api";
import { EmptyState } from "@/components/ui/empty-state";
import { FOCUS_RING } from "@/lib/focus-ring";

interface SessionSidebarProps {
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  refreshKey?: number;
}

export function SessionSidebar({
  activeConversationId,
  onSelectConversation,
  onNewChat,
  refreshKey,
}: SessionSidebarProps) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchConversations()
      .then((data) => { if (!cancelled) setConversations(data); })
      .catch(() => { /* silently fail — sidebar is non-critical */ });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.conversation_id !== id));
    } catch {
      // ignore
    }
  };

  return (
    <div className="w-full flex flex-col bg-surface-0 h-full">
      <div className="p-3 border-b border-b-secondary flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Conversations
        </h3>
        <button
          onClick={onNewChat}
          className="text-xs px-2 py-1 rounded-[var(--radius-sm)] bg-j-primary hover:bg-j-primary-hover text-j-primary-fg cursor-pointer"
        >
          + New
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
        {conversations.length === 0 ? (
          <EmptyState title="No conversations yet" description="Start a chat to see your history here" />
        ) : (
          conversations.map((convo) => (
            <div
              key={convo.conversation_id}
              role="button"
              tabIndex={0}
              aria-selected={activeConversationId === convo.conversation_id}
              onClick={() => onSelectConversation(convo.conversation_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectConversation(convo.conversation_id);
                }
              }}
              className={`rounded-[var(--radius-sm)] px-2.5 py-2 hover:bg-surface-2 transition-colors duration-150 group cursor-pointer ${FOCUS_RING} ${
                activeConversationId === convo.conversation_id
                  ? "bg-j-primary-soft border-l-2 border-l-j-primary"
                  : ""
              }`}
            >
              <div className="flex items-start justify-between gap-1">
                <p className="text-xs text-t-primary truncate flex-1 font-medium">
                  {convo.title || convo.preview || "New conversation"}
                </p>
                <button
                  onClick={(e) => handleDelete(e, convo.conversation_id)}
                  className="opacity-0 group-hover:opacity-100 text-t-muted hover:text-j-error text-[10px] shrink-0 cursor-pointer"
                  title="Archive"
                >
                  {"\u2715"}
                </button>
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-[10px] text-t-muted">
                  {convo.message_count} msgs
                </span>
                {convo.total_cost_usd > 0 && (
                  <span className="text-[10px] text-t-muted">
                    ${convo.total_cost_usd.toFixed(4)}
                  </span>
                )}
                {convo.last_active_at && (
                  <span className="text-[10px] text-t-muted">
                    {formatRelativeTime(convo.last_active_at)}
                  </span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  return `${diffDays}d ago`;
}

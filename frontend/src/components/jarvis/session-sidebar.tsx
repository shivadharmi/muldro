"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchConversations,
  deleteConversation,
  type ConversationSummary,
} from "@/lib/api";

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
  const [collapsed, setCollapsed] = useState(false);

  const loadConversations = useCallback(async () => {
    try {
      const data = await fetchConversations();
      setConversations(data);
    } catch {
      // silently fail — sidebar is non-critical
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations, refreshKey]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.conversation_id !== id));
    } catch {
      // ignore
    }
  };

  if (collapsed) {
    return (
      <div className="w-10 border-r border-neutral-800 flex flex-col items-center pt-4">
        <button
          onClick={() => setCollapsed(false)}
          className="text-neutral-500 hover:text-neutral-300 text-sm cursor-pointer"
          title="Expand sidebar"
        >
          {"\u25B6"}
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 border-r border-neutral-800 flex flex-col bg-neutral-950">
      <div className="p-3 border-b border-neutral-800 flex items-center justify-between">
        <h3 className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
          Conversations
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={onNewChat}
            className="text-xs px-2 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white cursor-pointer"
          >
            + New
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="text-neutral-500 hover:text-neutral-300 text-sm ml-1 cursor-pointer"
            title="Collapse sidebar"
          >
            {"\u25C0"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {conversations.length === 0 ? (
          <p className="text-neutral-600 text-xs text-center mt-8 px-4">
            No conversations yet. Start a new chat!
          </p>
        ) : (
          conversations.map((convo) => (
            <button
              key={convo.conversation_id}
              onClick={() => onSelectConversation(convo.conversation_id)}
              className={`w-full text-left px-3 py-2.5 border-b border-neutral-900 hover:bg-neutral-800/50 transition-colors group cursor-pointer ${
                activeConversationId === convo.conversation_id
                  ? "bg-neutral-800/70 border-l-2 border-l-blue-500"
                  : ""
              }`}
            >
              <div className="flex items-start justify-between gap-1">
                <p className="text-xs text-neutral-300 truncate flex-1">
                  {convo.preview || "New conversation"}
                </p>
                <button
                  onClick={(e) => handleDelete(e, convo.conversation_id)}
                  className="opacity-0 group-hover:opacity-100 text-neutral-600 hover:text-red-400 text-[10px] shrink-0 cursor-pointer"
                  title="Archive"
                >
                  {"\u2715"}
                </button>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-[10px] text-neutral-600">
                  {convo.message_count} msgs
                </span>
                {convo.last_active_at && (
                  <span className="text-[10px] text-neutral-600">
                    {formatRelativeTime(convo.last_active_at)}
                  </span>
                )}
              </div>
            </button>
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

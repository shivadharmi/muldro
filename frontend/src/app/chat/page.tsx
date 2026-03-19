"use client";

import { useCallback, useState } from "react";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceState } from "@/hooks/use-surface-state";
import { fetchConversationMessages, type ConversationMessage } from "@/lib/api";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const { surfaces, upsertSurface } = useSurfaceState();
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [initialMessages, setInitialMessages] = useState<
    ConversationMessage[]
  >([]);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);
  const [showSessions, setShowSessions] = useState(false);
  const [showContext, setShowContext] = useState(false);

  const { connected, sendAction } = useJarvisWs({
    userId,
    onSurface: upsertSurface,
    enabled: !!user,
  });

  const handleAction = useCallback(
    (actionType: string, payload: Record<string, unknown>) => {
      handleA2UIAction(sendAction, actionType, payload);
    },
    [sendAction]
  );

  const handleSelectConversation = useCallback(
    async (conversationId: string) => {
      setActiveConversationId(conversationId);
      setShowSessions(false);
      try {
        const data = await fetchConversationMessages(conversationId);
        setInitialMessages(data.messages);
      } catch {
        setInitialMessages([]);
      }
    },
    []
  );

  const handleNewChat = useCallback(() => {
    setActiveConversationId(null);
    setInitialMessages([]);
    setShowSessions(false);
  }, []);

  const handleConversationCreated = useCallback((id: string) => {
    setActiveConversationId(id);
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  const handleMessageSent = useCallback(() => {
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  if (!user) return null;

  return (
    <div className="flex h-screen relative">
      {/* A. Collapsible Session Panel (left) */}
      <div
        className={`
          ${showSessions ? "w-[280px]" : "w-0"}
          transition-[width] duration-200 overflow-hidden
          border-r border-b-secondary bg-surface-0 shrink-0
          hidden md:block
        `}
      >
        <SessionSidebar
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
          onNewChat={handleNewChat}
          refreshKey={sidebarRefreshKey}
        />
      </div>

      {/* Mobile session panel */}
      {showSessions && (
        <>
          <div
            className="fixed inset-0 bg-black/30 z-20 md:hidden"
            onClick={() => setShowSessions(false)}
          />
          <div className="fixed inset-y-0 left-0 z-30 w-[280px] bg-surface-0 border-r border-b-secondary md:hidden">
            <SessionSidebar
              activeConversationId={activeConversationId}
              onSelectConversation={handleSelectConversation}
              onNewChat={handleNewChat}
              refreshKey={sidebarRefreshKey}
            />
          </div>
        </>
      )}

      {/* B. Full-Width Chat (center) */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat header */}
        <div className="px-4 py-2.5 border-b border-b-secondary flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSessions(!showSessions)}
              className="p-1.5 rounded-[var(--radius-sm)] hover:bg-surface-2 text-t-tertiary hover:text-t-primary transition-colors cursor-pointer"
              aria-label="Toggle sessions"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M3 4h10M3 8h10M3 12h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
            <h2 className="text-sm font-medium text-t-primary">Chat</h2>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <span
                className={`w-2 h-2 rounded-full ${
                  connected ? "bg-j-success" : "bg-j-error"
                }`}
              />
              <span className="text-t-tertiary">
                {connected ? "Connected" : "Offline"}
              </span>
            </div>
          </div>
        </div>

        {/* A2UI Surfaces — rendered inline above chat when present */}
        {surfaces.length > 0 && (
          <div className="border-b border-b-secondary overflow-y-auto max-h-[40vh] p-4 space-y-4 bg-surface-0">
            {surfaces.map((surface) => (
              <A2UIRenderer
                key={surface.id}
                surface={surface}
                onAction={handleAction}
              />
            ))}
          </div>
        )}

        {/* Chat messages — full width */}
        <ChatPanel
          conversationId={activeConversationId}
          initialMessages={initialMessages}
          onConversationCreated={handleConversationCreated}
          onMessageSent={handleMessageSent}
        />
      </div>

      {/* C. Context Panel (right, triggered) */}
      {showContext && (
        <div className="w-80 border-l border-b-secondary bg-surface-1 overflow-y-auto p-4 shrink-0">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-t-primary">Context</h3>
            <button
              onClick={() => setShowContext(false)}
              className="p-1 rounded hover:bg-surface-2 text-t-muted cursor-pointer"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <p className="text-xs text-t-muted">
            Context panel shows memories referenced, trace links, and entity cards for Jarvis responses.
          </p>
        </div>
      )}
    </div>
  );
}

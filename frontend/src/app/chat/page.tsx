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
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<ConversationMessage[]>([]);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

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

  const handleSelectConversation = useCallback(async (conversationId: string) => {
    setActiveConversationId(conversationId);
    try {
      const data = await fetchConversationMessages(conversationId);
      setInitialMessages(data.messages);
    } catch {
      setInitialMessages([]);
    }
  }, []);

  const handleNewChat = useCallback(() => {
    setActiveConversationId(null);
    setInitialMessages([]);
  }, []);

  const handleConversationCreated = useCallback((id: string) => {
    setActiveConversationId(id);
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  const handleMessageSent = useCallback(() => {
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  if (!user) {
    return null;
  }

  return (
    <div className="flex h-screen">
      {/* Left: Session Sidebar */}
      <SessionSidebar
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        refreshKey={sidebarRefreshKey}
      />

      {/* Center: A2UI Surfaces */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <header className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">Chat</h1>
          <div className="flex items-center gap-2 text-xs">
            <span
              className={`inline-block w-2 h-2 rounded-full ${
                connected ? "bg-green-500" : "bg-red-500"
              }`}
            />
            <span className="text-neutral-500">
              {connected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </header>

        {surfaces.length === 0 ? (
          <div className="text-neutral-600 text-center mt-20">
            <p className="text-lg">No active surfaces</p>
            <p className="text-sm mt-2">
              Surfaces will appear here when Jarvis generates briefings,
              approvals, or other dynamic UI.
            </p>
          </div>
        ) : (
          surfaces.map((surface) => (
            <A2UIRenderer
              key={surface.id}
              surface={surface}
              onAction={handleAction}
            />
          ))
        )}
      </div>

      {/* Right: Chat Panel */}
      <div className="w-[400px] border-l border-neutral-800 flex flex-col">
        <div className="p-4 border-b border-neutral-800">
          <h2 className="text-sm font-medium text-neutral-400">Chat</h2>
        </div>
        <ChatPanel
          conversationId={activeConversationId}
          initialMessages={initialMessages}
          onConversationCreated={handleConversationCreated}
          onMessageSent={handleMessageSent}
        />
      </div>
    </div>
  );
}

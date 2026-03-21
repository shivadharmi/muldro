"use client";

import { useCallback, useState } from "react";
import { GeneratedSurfaceCard } from "@/components/primitives/generated-surface";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import { fetchConversationMessages, type ConversationMessage } from "@/lib/api";
import type { A2UISurface } from "@/lib/a2ui-types";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const surfaces = useSurfaceStore((s) => s.surfaces);
  const addSurface = useSurfaceStore((s) => s.addSurface);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const togglePin = useSurfaceStore((s) => s.togglePin);

  // Bridge: A2UISurface (from WebSocket) → GeneratedSurface (store)
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
      addSurface({
        id: ws.id,
        kind: "summary",
        title: String(ws.metadata?.title ?? "Surface"),
        data: ws.metadata ?? {},
        created_at: new Date().toISOString(),
        pinned: false,
        source_message_id: null,
      });
    },
    [addSurface]
  );
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [initialMessages, setInitialMessages] = useState<
    ConversationMessage[]
  >([]);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  const { connected } = useJarvisWs({
    userId,
    onSurface: handleWsSurface,
    enabled: !!user,
  });

  const handleSelectConversation = useCallback(
    async (conversationId: string) => {
      setActiveConversationId(conversationId);
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
    <CommandWorkspace
      sessionRail={
        <SessionSidebar
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
          onNewChat={handleNewChat}
          refreshKey={sidebarRefreshKey}
        />
      }
      commandPanel={
        <div className="flex flex-col h-full">
          {/* Chat header */}
          <div className="px-4 py-2.5 border-b border-b-secondary flex items-center justify-between shrink-0">
            <h2 className="text-sm font-medium text-t-primary">Command</h2>
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

          {/* Chat messages */}
          <ChatPanel
            conversationId={activeConversationId}
            initialMessages={initialMessages}
            onConversationCreated={handleConversationCreated}
            onMessageSent={handleMessageSent}
          />
        </div>
      }
      surfaces={
        surfaces.length > 0 ? (
          <div className="space-y-3">
            {surfaces.map((surface) => (
              <GeneratedSurfaceCard
                key={surface.id}
                surface={surface}
                onPin={togglePin}
                onRemove={removeSurface}
              />
            ))}
          </div>
        ) : undefined
      }
    />
  );
}

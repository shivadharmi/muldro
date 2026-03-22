"use client";

import { useCallback, useMemo, useState } from "react";
import { GeneratedSurfaceCard } from "@/components/primitives/generated-surface";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import { useCommandStore } from "@/stores/command-store";
import { fetchConversationMessages, type ConversationMessage } from "@/lib/api";
import type { A2UISurface } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const allSurfaces = useSurfaceStore((s) => s.surfaces);
  const surfaces = useMemo(() => allSurfaces.filter((sf) => sf.position === "inline"), [allSurfaces]);
  const addSurface = useSurfaceStore((s) => s.addSurface);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const togglePin = useSurfaceStore((s) => s.togglePin);

  const { mode, setMode } = useCommandStore();

  // Bridge: A2UISurface (from WebSocket) → GeneratedSurface (store)
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
      addSurface({
        id: ws.id,
        kind: (ws.metadata?.kind as SurfaceKind) || "summary",
        title: String(ws.metadata?.title ?? "Surface"),
        data: ws.metadata ?? {},
        created_at: new Date().toISOString(),
        pinned: false,
        position: "inline",
        schema_version: 1,
        source_message_id: (ws.metadata?.source_message_id as string) ?? null,
        source_run_id: (ws.metadata?.source_run_id as string) ?? null,
        source_artifact_id: (ws.metadata?.source_artifact_id as string) ?? null,
      });
    },
    [addSurface]
  );

  // Restore active conversation from global store (survives navigation)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    () => useCommandStore.getState().conversationId
  );
  const [initialMessages, setInitialMessages] = useState<ConversationMessage[]>([]);
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
    useCommandStore.getState().setCachedMessages([]);
    useCommandStore.getState().setConversationId(null);
  }, []);

  const handleConversationCreated = useCallback((id: string) => {
    setActiveConversationId(id);
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  const handleMessageSent = useCallback(() => {
    setSidebarRefreshKey((k) => k + 1);
  }, []);

  if (!user) return null;

  const MODES = [
    { value: "ask" as const, label: "Ask" },
    { value: "plan" as const, label: "Plan" },
    { value: "execute" as const, label: "Execute" },
  ];

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
          {/* Command header with mode + connection */}
          <div className="px-4 py-2.5 border-b border-b-secondary flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  onClick={() => setMode(m.value)}
                  className={`px-2.5 py-1 text-xs rounded-full transition-colors cursor-pointer ${
                    mode === m.value
                      ? "bg-accent-primary text-white"
                      : "text-t-tertiary hover:text-t-secondary hover:bg-surface-1"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
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

          {/* Chat panel */}
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

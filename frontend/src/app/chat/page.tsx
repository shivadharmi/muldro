"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceState } from "@/hooks/use-surface-state";
import { useSurfaceStore } from "@/stores/surface-store";
import { useCommandStore } from "@/stores/command-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { fetchConversationMessages, type ConversationMessage } from "@/lib/api";
import type { A2UISurface } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const allSurfaces = useSurfaceStore((s) => s.surfaces);
  const surfaces = useMemo(() => allSurfaces.filter((sf) => sf.position === "inline"), [allSurfaces]);
  const addSurface = useSurfaceStore((s) => s.addSurface);

  const { mode, setMode } = useCommandStore();
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);
  const { surfaces: a2uiSurfaces, upsertSurface } = useSurfaceState();
  const dockableA2UISurfaces = useMemo(
    () => surfaces.filter((surface) => !!surface.data?.a2ui_surface),
    [surfaces]
  );

  // Bridge: A2UISurface (from WebSocket) -> surface stores
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
      // Primary: keep full protocol surface for A2UI-native render path
      upsertSurface(ws);

      // Keep workspace/shell surface store synchronized for docking + layout state
      addSurface({
        id: ws.id,
        kind: (ws.metadata?.kind as SurfaceKind) || "summary",
        title: String(ws.metadata?.title ?? "Surface"),
        data: { ...(ws.metadata ?? {}), a2ui_surface: ws },
        created_at: new Date().toISOString(),
        pinned: false,
        position: "inline",
        schema_version: 1,
        source_message_id: (ws.metadata?.source_message_id as string) ?? null,
        source_run_id: (ws.metadata?.source_run_id as string) ?? null,
        source_artifact_id: (ws.metadata?.source_artifact_id as string) ?? null,
      });
    },
    [addSurface, upsertSurface]
  );

  const handleWsSurfaceUpdate = useCallback(
    (_surfaceId: string, ws: A2UISurface) => {
      upsertSurface(ws);
      addSurface({
        id: ws.id,
        kind: (ws.metadata?.kind as SurfaceKind) || "summary",
        title: String(ws.metadata?.title ?? "Surface"),
        data: { ...(ws.metadata ?? {}), a2ui_surface: ws },
        created_at: new Date().toISOString(),
        pinned: false,
        position: "inline",
        schema_version: 1,
        source_message_id: (ws.metadata?.source_message_id as string) ?? null,
        source_run_id: (ws.metadata?.source_run_id as string) ?? null,
        source_artifact_id: (ws.metadata?.source_artifact_id as string) ?? null,
      });
    },
    [addSurface, upsertSurface]
  );

  // Restore active conversation from global store (survives navigation)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    () => useCommandStore.getState().conversationId
  );
  const [initialMessages, setInitialMessages] = useState<ConversationMessage[]>([]);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  const { connected, sendAction } = useJarvisWs({
    userId,
    onSurface: handleWsSurface,
    onSurfaceUpdate: handleWsSurfaceUpdate,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(() => sendAction);
  }, [sendAction, setGlobalSendAction]);

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
        a2uiSurfaces.length > 0 || dockableA2UISurfaces.length > 0 ? (
          <div className="space-y-3">
            {a2uiSurfaces.map((surface) => (
              <A2UIRenderer
                key={`a2ui-${surface.id}`}
                surface={surface}
                onAction={(action, payload) =>
                  handleA2UIAction(sendAction, action, payload)
                }
              />
            ))}
            {dockableA2UISurfaces.map((surface) => (
                <A2UIRenderer
                  key={`surface-${surface.id}`}
                  surface={surface.data.a2ui_surface as A2UISurface}
                  onAction={(action, payload) =>
                    handleA2UIAction(sendAction, action, payload)
                  }
                />
              ))}
          </div>
        ) : undefined
      }
    />
  );
}

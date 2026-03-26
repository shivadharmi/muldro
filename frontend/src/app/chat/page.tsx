"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
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
  const inlineSurfaces = useMemo(
    () => allSurfaces.filter((sf) => sf.position === "inline"),
    [allSurfaces]
  );
  const addSurface = useSurfaceStore((s) => s.addSurface);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const setPosition = useSurfaceStore((s) => s.setPosition);

  const { mode, setMode } = useCommandStore();
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);

  // Single store: WebSocket surfaces go to useSurfaceStore only
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
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
    [addSurface]
  );

  const handleWsSurfaceUpdate = useCallback(
    (_surfaceId: string, ws: A2UISurface) => {
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
    [addSurface]
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

  // Handle surfaces arriving through the SSE chat stream (direct delivery,
  // no WebSocket/Redis dependency)
  const handleSSESurface = useCallback(
    (surface: { id: string; children: unknown[]; metadata: Record<string, unknown> }) => {
      addSurface({
        id: surface.id,
        kind: (surface.metadata?.kind as SurfaceKind) || "summary",
        title: String(surface.metadata?.title ?? "Surface"),
        data: {
          ...(surface.metadata ?? {}),
          a2ui_surface: {
            type: "surface" as const,
            id: surface.id,
            children: surface.children as A2UISurface["children"],
            metadata: surface.metadata,
          },
        },
        created_at: new Date().toISOString(),
        pinned: false,
        position: "inline",
        schema_version: 1,
        source_message_id: (surface.metadata?.source_message_id as string) ?? null,
        source_run_id: (surface.metadata?.source_run_id as string) ?? null,
        source_artifact_id: null,
      });
    },
    [addSurface]
  );

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
            onSurface={handleSSESurface}
          />
        </div>
      }
      surfaces={
        inlineSurfaces.length > 0 ? (
          <div className="p-3 space-y-3">
            {/* Surface panel header */}
            <div className="flex items-center justify-between px-1">
              <span className="text-xs font-medium text-t-secondary">
                Surfaces ({inlineSurfaces.length})
              </span>
            </div>

            {/* Surface cards */}
            {inlineSurfaces.map((surface) => (
              <div
                key={surface.id}
                className="rounded-xl border border-b-primary bg-surface-0 overflow-hidden"
              >
                {/* Mini header: title + controls */}
                <div className="flex items-center justify-between px-3 py-2 border-b border-b-primary bg-surface-1/50">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-medium text-t-primary truncate">
                      {surface.title}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-t-tertiary capitalize shrink-0">
                      {surface.kind}
                    </span>
                  </div>
                  <div className="flex gap-0.5 shrink-0">
                    <button
                      onClick={() => setPosition(surface.id, "center-pane")}
                      title="Expand"
                      className="p-1 rounded text-t-tertiary hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer text-xs"
                    >
                      &#9634;
                    </button>
                    <button
                      onClick={() => removeSurface(surface.id)}
                      title="Dismiss"
                      className="p-1 rounded text-t-tertiary hover:text-red-400 hover:bg-surface-2 transition-colors cursor-pointer text-xs"
                    >
                      &#10005;
                    </button>
                  </div>
                </div>

                {/* A2UI content */}
                <div className="p-3">
                  {surface.data?.a2ui_surface ? (
                    <A2UIRenderer
                      surface={surface.data.a2ui_surface as A2UISurface}
                      onAction={(action, payload) =>
                        handleA2UIAction(sendAction, action, payload)
                      }
                    />
                  ) : (
                    <p className="text-xs text-t-tertiary">
                      {surface.title}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : undefined
      }
    />
  );
}

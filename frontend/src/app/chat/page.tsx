"use client";

import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "@/components/jarvis/chat-panel";
import { SessionSidebar } from "@/components/jarvis/session-sidebar";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { SurfaceCard } from "@/components/workspace/surface-card";
import { SurfaceDetailModal } from "@/components/workspace/surface-detail-modal";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import type { WorkspaceSurface } from "@/stores/surface-store";
import { useCommandStore } from "@/stores/command-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { fetchConversationMessages, type ConversationMessage } from "@/lib/api";
import type { WorkspaceSurfacePush, SurfacePreview } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const surfaces = useSurfaceStore((s) => s.surfaces);
  const addSurface = useSurfaceStore((s) => s.addSurface);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const detailModalOpen = useSurfaceStore((s) => s.detailModalOpen);
  const openDetailModal = useSurfaceStore((s) => s.openDetailModal);
  const closeDetailModal = useSurfaceStore((s) => s.closeDetailModal);

  const { mode, setMode } = useCommandStore();
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);

  const handleSurfacePush = useCallback(
    (push: WorkspaceSurfacePush) => {
      addSurface({
        id: push.id,
        kind: (push.kind as SurfaceKind) || "summary",
        preview: push.preview,
        detail_config: push.detail_config,
        source_run_id: push.source_run_id,
        response_preview: push.response_preview,
        created_at: push.created_at || new Date().toISOString(),
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
    onSurfacePush: handleSurfacePush,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(sendAction);
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

  // SSE surfaces from chat stream → convert to WorkspaceSurface
  const handleSSESurface = useCallback(
    (surface: { id: string; children: unknown[]; metadata: Record<string, unknown> }) => {
      const meta = surface.metadata ?? {};
      const preview: SurfacePreview = {
        title: String(meta.title ?? "Surface"),
        subtitle: meta.reasoning ? String(meta.reasoning).slice(0, 120) : null,
        status: null,
        priority: (meta.priority as SurfacePreview["priority"]) ?? null,
        metrics: [],
        entities: [],
        progress: null,
        timestamp: new Date().toISOString(),
        tags: [],
      };
      addSurface({
        id: surface.id,
        kind: (meta.kind as SurfaceKind) || "summary",
        preview,
        detail_config: null,
        source_run_id: (meta.source_run_id as string) ?? null,
        response_preview: (meta.response_preview as string) ?? null,
        created_at: new Date().toISOString(),
      });
    },
    [addSurface]
  );

  const activeSurface = activeSurfaceId
    ? surfaces.find((s) => s.id === activeSurfaceId) ?? null
    : null;

  if (!user) return null;

  const MODES = [
    { value: "ask" as const, label: "Ask" },
    { value: "plan" as const, label: "Plan" },
    { value: "execute" as const, label: "Execute" },
  ];

  return (
    <>
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
          surfaces.length > 0 ? (
            <div className="p-3 space-y-3">
              <div className="flex items-center justify-between px-1">
                <span className="text-xs font-medium text-t-secondary">
                  Surfaces ({surfaces.length})
                </span>
              </div>

              {surfaces.map((surface) => (
                <SurfaceCard
                  key={surface.id}
                  surface={surface}
                  onClick={() => openDetailModal(surface.id)}
                />
              ))}
            </div>
          ) : undefined
        }
      />

      {activeSurface && (
        <SurfaceDetailModal
          surface={activeSurface}
          open={detailModalOpen}
          onClose={closeDetailModal}
        />
      )}
    </>
  );
}

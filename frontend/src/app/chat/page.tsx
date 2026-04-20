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
import type { WorkspaceSurfacePush, SurfacePreview, SurfaceUpdate } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const surfaces = useSurfaceStore((s) => s.surfaces);
  const addSurface = useSurfaceStore((s) => s.addSurface);
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const detailModalOpen = useSurfaceStore((s) => s.detailModalOpen);
  const openDetailModal = useSurfaceStore((s) => s.openDetailModal);
  const closeDetailModal = useSurfaceStore((s) => s.closeDetailModal);
  const updateSurface = useSurfaceStore((s) => s.updateSurface);

  const { mode, setMode } = useCommandStore();
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);

  const handleSurfacePush = useCallback(
    (push: WorkspaceSurfacePush) => {
      addSurface({
        id: push.id,
        kind: push.kind || "summary",
        preview: push.preview,
        detail_config: push.detail_config,
        source_run_id: push.source_run_id,
        response_preview: push.response_preview,
        created_at: push.created_at || new Date().toISOString(),
        surface_data: push.surface_data ?? null,
        // Forward the insight payload — previously dropped here because
        // the WorkspaceSurfacePush type omitted the field, so insight
        // surfaces rendered with empty details even though the backend
        // sent the data.
        insight_data: push.insight_data ?? null,
        // Live execution fields: when the run surface is pushed (REST or
        // WS), these let the run renderer show the current phase/steps
        // without waiting for a separate surface_update message.
        phase: push.phase ?? null,
        steps: push.steps ?? null,
        current_step: push.current_step ?? null,
        progress: push.progress ?? null,
        approval: push.approval ?? null,
        results: push.results ?? null,
        trust_context: push.trust_context ?? null,
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
    onSurfaceUpdate: useCallback(
      (update: SurfaceUpdate) => updateSurface(update.surface_id, update),
      [updateSurface]
    ),
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
                <div className="bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5">
                  {MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setMode(m.value)}
                      className={`px-3.5 py-1.5 text-[13px] rounded-[var(--radius-md)] transition-all duration-150 cursor-pointer ${
                        mode === m.value
                          ? "bg-j-primary text-j-primary-fg font-medium"
                          : "text-t-muted hover:text-t-secondary"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-1.5 text-xs">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    connected ? "bg-j-success" : "bg-j-error"
                  }`}
                />
                <span className="text-t-muted">
                  {connected ? "Connected" : "Offline"}
                </span>
              </div>
            </div>

            {/* Connection warning */}
            {!connected && (
              <div className="px-4 py-2 bg-j-warning-soft border-b border-j-warning/20 flex items-center gap-2 text-xs text-j-warning animate-fade-in">
                <span className="w-1.5 h-1.5 rounded-full bg-j-warning animate-pulse-live" />
                Connection lost — reconnecting...
              </div>
            )}

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
            <div className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-t-secondary">
                  Surfaces
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-muted font-medium">
                  {surfaces.length}
                </span>
              </div>

              {[...surfaces]
                .sort((a, b) => {
                  const isActive = (s: WorkspaceSurface) =>
                    s.phase === "executing" || s.phase === "approval_needed" || s.phase === "planning";
                  const aActive = isActive(a) ? 0 : 1;
                  const bActive = isActive(b) ? 0 : 1;
                  if (aActive !== bActive) return aActive - bActive;
                  return b.created_at.localeCompare(a.created_at);
                })
                .map((surface) => (
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

"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChatPanel } from "@/components/muldro/chat-panel";
import { SessionSidebar } from "@/components/muldro/session-sidebar";
import { CommandWorkspace } from "@/components/feature/command/command-workspace";
import { UnitCard } from "@/components/workspace/unit-card";
import { UnitDetail } from "@/components/workspace/unit-detail";
import { PreparedQueue } from "@/components/workspace/prepared-queue";
import { useAuth } from "@/lib/auth";
import { useMuldroWs } from "@/hooks/use-muldro-ws";
import { useUnitStore } from "@/stores/unit-store";
import { useCommandStore } from "@/stores/command-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import {
  fetchConversationMessages,
  fetchWorkspaceUnits,
  type ConversationMessage,
} from "@/lib/api";
import { formatApiError, type ParsedApiError } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import type { Unit } from "@/lib/types/unit";

export default function ChatPage() {
  const { user } = useAuth();
  const userId = user?.user_id ?? "";

  const units = useUnitStore((s) => s.units);
  const setUnits = useUnitStore((s) => s.setUnits);
  const upsertUnit = useUnitStore((s) => s.upsertUnit);
  const activeKey = useUnitStore((s) => s.activeKey);
  const detailOpen = useUnitStore((s) => s.detailOpen);
  const openDetail = useUnitStore((s) => s.openDetail);
  const closeDetail = useUnitStore((s) => s.closeDetail);

  const { permissionMode, setPermissionMode } = useCommandStore();
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);
  const { addToast } = useToast();

  const handleWsError = useCallback(
    (err: ParsedApiError) => addToast(formatApiError(err), "error"),
    [addToast]
  );

  // The rail reads the SAME feed the workspace does. A chat turn produces a
  // Unit when it created a durable ROW, and code builds that frame from the
  // row — not from a preview the chat stream hand-assembled.
  const { data: unitData } = useQuery({
    queryKey: ["workspace-units"],
    queryFn: fetchWorkspaceUnits,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (unitData?.units) setUnits(unitData.units);
  }, [unitData, setUnits]);

  const handleUnitPush = useCallback((unit: Unit) => upsertUnit(unit), [upsertUnit]);

  // Restore active conversation from global store (survives navigation)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    () => useCommandStore.getState().conversationId
  );
  const [initialMessages, setInitialMessages] = useState<ConversationMessage[]>([]);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  const { connected, sendAction } = useMuldroWs({
    userId,
    onUnitPush: handleUnitPush,
    onError: handleWsError,
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

  const active = activeKey ? units.find((u) => u.frame.key === activeKey) ?? null : null;

  if (!user) return null;

  const MODES = [
    { value: "auto" as const, label: "Auto" },
    { value: "ask" as const, label: "Ask" },
    { value: "bypass" as const, label: "Bypass" },
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
                      onClick={() => setPermissionMode(m.value)}
                      className={`px-3.5 py-1.5 text-[13px] rounded-[var(--radius-md)] transition-all duration-150 cursor-pointer ${
                        permissionMode === m.value
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
            />
          </div>
        }
        surfaces={
          units.length > 0 ? (
            <div className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-t-secondary">Workspace</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-muted font-medium">
                  {units.length}
                </span>
              </div>

              {/* The server's rank order, rendered as given. No client sort:
                  re-sorting a list the server already ranked throws the
                  ranking away and puts arrival order back. */}
              {units.map((u) => (
                <UnitCard
                  key={u.frame.key}
                  unit={u}
                  onOpen={() => openDetail(u.frame.key)}
                />
              ))}
            </div>
          ) : undefined
        }
      />

      <UnitDetail unit={active} open={detailOpen} onClose={closeDetail}>
        {active?.frame.entity_type === "prepared_work" && <PreparedQueue />}
      </UnitDetail>
    </>
  );
}

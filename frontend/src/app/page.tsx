"use client";

import { useCallback, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchWorkspaceSurfaces,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { WorkspaceStatusBar } from "@/components/workspace/workspace-status-bar";
import { WorkspaceCanvas } from "@/components/workspace/workspace-canvas";
import type { A2UISurface } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function WorkspacePage() {
  const { user } = useAuth();
  const { addSurface } = useSurfaceStore();
  const wsSurfaces = useSurfaceStore((s) => s.surfaces);
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);

  const { data: system } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  // Single unified API: returns pre-built A2UI surfaces with populated children
  const { data: workspaceData } = useQuery({
    queryKey: ["workspace-surfaces"],
    queryFn: fetchWorkspaceSurfaces,
    refetchInterval: 15_000,
  });

  // Merge REST surfaces with real-time WS surfaces (WS wins on duplicate IDs)
  const allSurfaces = useMemo(() => {
    const restSurfaces: A2UISurface[] = workspaceData?.surfaces ?? [];
    const wsWorkspaceSurfaces = wsSurfaces
      .filter((s) => s.position === "workspace" && s.data?.a2ui_surface)
      .map((s) => s.data.a2ui_surface as A2UISurface);

    // Deduplicate: WS surfaces override REST surfaces with same ID
    const surfaceMap = new Map<string, A2UISurface>();
    for (const s of restSurfaces) {
      surfaceMap.set(s.id, s);
    }
    for (const s of wsWorkspaceSurfaces) {
      surfaceMap.set(s.id, s);
    }
    return Array.from(surfaceMap.values());
  }, [workspaceData, wsSurfaces]);

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  // Derive greeting data from surfaces
  const approvalCount = allSurfaces.filter(
    (s) => (s.metadata as Record<string, unknown>)?.kind === "approval"
  ).length;
  const briefingSurface = allSurfaces.find(
    (s) => (s.metadata as Record<string, unknown>)?.kind === "briefing"
  );
  const headline = briefingSurface
    ? String((briefingSurface.metadata as Record<string, unknown>)?.title ?? "")
    : null;

  // WebSocket: proactive surfaces from Jarvis go to workspace position
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
      addSurface({
        id: ws.id,
        kind: (ws.metadata?.kind as SurfaceKind) || "summary",
        title: String(ws.metadata?.title ?? "Update"),
        data: { ...(ws.metadata ?? {}), a2ui_surface: ws },
        created_at: new Date().toISOString(),
        pinned: false,
        position: ws.metadata?.source_message_id ? "inline" : "workspace",
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
        title: String(ws.metadata?.title ?? "Update"),
        data: { ...(ws.metadata ?? {}), a2ui_surface: ws },
        created_at: new Date().toISOString(),
        pinned: false,
        position: ws.metadata?.source_message_id ? "inline" : "workspace",
        schema_version: 1,
        source_message_id: (ws.metadata?.source_message_id as string) ?? null,
        source_run_id: (ws.metadata?.source_run_id as string) ?? null,
        source_artifact_id: (ws.metadata?.source_artifact_id as string) ?? null,
      });
    },
    [addSurface]
  );

  const { sendAction } = useJarvisWs({
    userId: user?.user_id ?? "",
    onSurface: handleWsSurface,
    onSurfaceUpdate: handleWsSurfaceUpdate,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(() => sendAction);
  }, [sendAction, setGlobalSendAction]);

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Greeting */}
      <GreetingHero
        headline={headline}
        approvalCount={approvalCount}
        sourceCount={sourceCount}
      />

      {/* System status bar */}
      <WorkspaceStatusBar system={system} />

      {/* Living canvas of A2UI surfaces */}
      <WorkspaceCanvas surfaces={allSurfaces} />
    </div>
  );
}

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
import type { WorkspaceSurface } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { WorkspaceStatusBar } from "@/components/workspace/workspace-status-bar";
import { WorkspaceCanvas } from "@/components/workspace/workspace-canvas";
import { SurfaceDetailModal } from "@/components/workspace/surface-detail-modal";
import type { WorkspaceSurfacePush } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function WorkspacePage() {
  const { user } = useAuth();
  const { addSurface } = useSurfaceStore();
  const wsSurfaces = useSurfaceStore((s) => s.surfaces);
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const detailModalOpen = useSurfaceStore((s) => s.detailModalOpen);
  const openDetailModal = useSurfaceStore((s) => s.openDetailModal);
  const closeDetailModal = useSurfaceStore((s) => s.closeDetailModal);
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);

  const { data: system } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: workspaceData } = useQuery({
    queryKey: ["workspace-surfaces"],
    queryFn: fetchWorkspaceSurfaces,
    refetchInterval: 15_000,
  });

  // Convert REST response to WorkspaceSurface
  const restSurfaces = useMemo((): WorkspaceSurface[] => {
    const raw = workspaceData?.surfaces ?? [];
    return raw.map((s) => ({
      id: s.id,
      kind: (s.kind as SurfaceKind) || "summary",
      preview: s.preview,
      detail_config: s.detail_config,
      decision: s.decision ?? null,
      source_run_id: s.source_run_id ?? null,
      response_preview: s.response_preview ?? null,
      created_at: s.created_at ?? new Date().toISOString(),
    }));
  }, [workspaceData]);

  // Merge REST + WS surfaces (WS wins on duplicate IDs)
  const allSurfaces = useMemo(() => {
    const map = new Map<string, WorkspaceSurface>();
    for (const s of restSurfaces) map.set(s.id, s);
    for (const s of wsSurfaces) map.set(s.id, s);
    return Array.from(map.values());
  }, [restSurfaces, wsSurfaces]);

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  const approvalCount = allSurfaces.filter((s) => s.kind === "approval").length;
  const briefing = allSurfaces.find((s) => s.kind === "briefing");
  const headline = briefing?.preview.title ?? null;

  // WS push → store
  const handleSurfacePush = useCallback(
    (push: WorkspaceSurfacePush) => {
      addSurface({
        id: push.id,
        kind: (push.kind as SurfaceKind) || "summary",
        preview: push.preview,
        detail_config: push.detail_config,
        decision: push.decision,
        source_run_id: push.source_run_id,
        response_preview: push.response_preview,
        created_at: push.created_at || new Date().toISOString(),
      });
    },
    [addSurface]
  );

  const { sendAction } = useJarvisWs({
    userId: user?.user_id ?? "",
    onSurfacePush: handleSurfacePush,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(sendAction);
  }, [sendAction, setGlobalSendAction]);

  const activeSurface = activeSurfaceId
    ? allSurfaces.find((s) => s.id === activeSurfaceId) ?? null
    : null;

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <GreetingHero
        headline={headline}
        approvalCount={approvalCount}
        sourceCount={sourceCount}
      />

      <WorkspaceStatusBar system={system} />

      <WorkspaceCanvas
        surfaces={allSurfaces}
        onSurfaceClick={openDetailModal}
      />

      {activeSurface && (
        <SurfaceDetailModal
          surface={activeSurface}
          open={detailModalOpen}
          onClose={closeDetailModal}
        />
      )}
    </div>
  );
}

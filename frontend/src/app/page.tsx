"use client";

import { useCallback, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchWorkspaceSurfaces,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resolveFirstRunState } from "@/lib/first-run-state";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import type { WorkspaceSurface } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { formatApiError, type ParsedApiError } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { BriefingGatheringCard } from "@/components/dashboard/briefing-gathering-card";
import { OnboardingCard } from "@/components/dashboard/onboarding-card";
import { WorkspaceCanvas } from "@/components/workspace/workspace-canvas";
import { SurfaceDetailModal } from "@/components/workspace/surface-detail-modal";
import type { WorkspaceSurfacePush, SurfaceUpdate } from "@/lib/a2ui-types";

export default function WorkspacePage() {
  const { user } = useAuth();
  const { addSurface } = useSurfaceStore();
  const updateSurface = useSurfaceStore((s) => s.updateSurface);
  const wsSurfaces = useSurfaceStore((s) => s.surfaces);
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const detailModalOpen = useSurfaceStore((s) => s.detailModalOpen);
  const openDetailModal = useSurfaceStore((s) => s.openDetailModal);
  const closeDetailModal = useSurfaceStore((s) => s.closeDetailModal);
  const setGlobalSendAction = useWsActionStore((s) => s.setSendAction);
  const { addToast } = useToast();

  const handleWsError = useCallback(
    (err: ParsedApiError) => addToast(formatApiError(err), "error"),
    [addToast]
  );

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
      kind: s.kind || "summary",
      preview: s.preview,
      detail_config: s.detail_config,
      source_run_id: s.source_run_id ?? null,
      response_preview: s.response_preview ?? null,
      created_at: s.created_at ?? new Date().toISOString(),
      surface_data: s.surface_data ?? null,
      // Execution state from persisted last_surface_update
      ...(s.phase && { phase: s.phase }),
      ...(s.steps && { steps: s.steps }),
      ...(s.current_step !== undefined && { current_step: s.current_step }),
      ...(s.progress && { progress: s.progress }),
      ...(s.approval && { approval: s.approval }),
      ...(s.results && { results: s.results }),
    }));
  }, [workspaceData]);

  // Merge REST + WS surfaces (WS wins on duplicate IDs)
  const allSurfaces = useMemo(() => {
    const map = new Map<string, WorkspaceSurface>();
    for (const s of restSurfaces) map.set(s.id, s);
    for (const s of wsSurfaces) map.set(s.id, s);
    const merged = Array.from(map.values());

    // Active executions first (executing or approval_needed), then by created_at desc
    const isActive = (s: WorkspaceSurface) =>
      s.phase === "executing" ||
      s.phase === "approval_needed" ||
      s.phase === "planning" ||
      s.kind === "proactive_insight";
    return merged.sort((a, b) => {
      const aActive = isActive(a) ? 0 : 1;
      const bActive = isActive(b) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      const dateCompare = b.created_at.localeCompare(a.created_at);
      return dateCompare !== 0 ? dateCompare : a.id.localeCompare(b.id);
    });
  }, [restSurfaces, wsSurfaces]);

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  const approvalCount = allSurfaces.filter((s) => s.kind === "approval").length;
  const briefing = allSurfaces.find((s) => s.kind === "briefing");
  const headline = briefing?.preview.title ?? null;
  // First-load state: onboarding (no source yet), gathering (source connected,
  // briefing pending), or active (briefing exists). See resolveFirstRunState.
  const firstRunState = resolveFirstRunState(sourceCount, Boolean(briefing));

  // WS push → store
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
      });
    },
    [addSurface]
  );

  const { sendAction } = useJarvisWs({
    userId: user?.user_id ?? "",
    onSurfacePush: handleSurfacePush,
    onSurfaceUpdate: useCallback(
      (update: SurfaceUpdate) => updateSurface(update.surface_id, update),
      [updateSurface]
    ),
    onError: handleWsError,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(sendAction);
  }, [sendAction, setGlobalSendAction]);

  const activeSurface = activeSurfaceId
    ? allSurfaces.find((s) => s.id === activeSurfaceId) ?? null
    : null;

  return (
    <div className="p-4 sm:p-6 space-y-4 animate-fade-in">
      <GreetingHero
        headline={headline}
        approvalCount={approvalCount}
        sourceCount={sourceCount}
        system={system}
      />

      {firstRunState === "onboarding" && <OnboardingCard />}
      {firstRunState === "gathering" && <BriefingGatheringCard />}

      {allSurfaces.length > 0 && (
        <WorkspaceCanvas
          surfaces={allSurfaces}
          onSurfaceClick={openDetailModal}
        />
      )}

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

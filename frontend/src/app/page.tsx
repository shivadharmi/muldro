"use client";

import { useCallback, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSystemDashboard,
  fetchCanvasDashboard,
  fetchHomeFeed,
  fetchApprovals,
  fetchSurfaces,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { useSurfaceStore } from "@/stores/surface-store";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { WorkspaceStatusBar } from "@/components/workspace/workspace-status-bar";
import { WorkspaceCanvas } from "@/components/workspace/workspace-canvas";
import type { A2UISurface } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export default function WorkspacePage() {
  const { user } = useAuth();
  const { addSurface } = useSurfaceStore();

  const { data: system } = useQuery({
    queryKey: ["system-dashboard"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: canvas } = useQuery({
    queryKey: ["canvas-dashboard"],
    queryFn: fetchCanvasDashboard,
    refetchInterval: 30_000,
  });

  const { data: homeFeed } = useQuery({
    queryKey: ["home-feed"],
    queryFn: fetchHomeFeed,
    refetchInterval: 15_000,
  });

  const { data: approvals = [] } = useQuery({
    queryKey: ["workspace-approvals"],
    queryFn: () => fetchApprovals("pending"),
    refetchInterval: 10_000,
  });

  // Hydrate surface store from persisted DB surfaces on first load
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current) return;
    hydratedRef.current = true;

    fetchSurfaces()
      .then((raw) => {
        const resp = raw as { surfaces?: { surface_id: string; surface_type: string; payload: Record<string, unknown>; created_at: string }[] };
        const surfaces = resp?.surfaces ?? [];
        for (const s of surfaces) {
          const meta = (s.payload?.metadata ?? {}) as Record<string, unknown>;
          addSurface({
            id: s.surface_id,
            kind: (meta.kind as SurfaceKind) || "summary",
            title: String(meta.title ?? "Surface"),
            data: meta,
            created_at: s.created_at,
            pinned: false,
            position: "workspace",
            schema_version: 1,
            source_message_id: (meta.source_message_id as string) ?? null,
            source_run_id: (meta.source_run_id as string) ?? null,
            source_artifact_id: null,
          });
        }
      })
      .catch(() => {
        // Persisted surfaces unavailable — workspace still works via REST polling
      });
  }, [addSurface]);

  const sourceCount = system?.observations
    ? Object.keys(system.observations).length
    : 0;

  // WebSocket: proactive surfaces from Jarvis go to workspace position
  const handleWsSurface = useCallback(
    (ws: A2UISurface) => {
      addSurface({
        id: ws.id,
        kind: (ws.metadata?.kind as SurfaceKind) || "summary",
        title: String(ws.metadata?.title ?? "Update"),
        data: ws.metadata ?? {},
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

  useJarvisWs({
    userId: user?.user_id ?? "",
    onSurface: handleWsSurface,
    enabled: !!user,
  });

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Greeting */}
      <GreetingHero
        headline={canvas?.headline ?? null}
        approvalCount={approvals.length}
        sourceCount={sourceCount}
      />

      {/* System status bar */}
      <WorkspaceStatusBar system={system} />

      {/* Living canvas of surfaces */}
      <WorkspaceCanvas
        approvals={approvals}
        briefingHeadline={canvas?.headline ?? null}
        recommendedActions={homeFeed?.recommended_actions ?? []}
        priorityItems={homeFeed?.priority_items ?? []}
      />
    </div>
  );
}

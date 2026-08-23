"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  dismissUnit,
  fetchRuntimeSummary,
  fetchSystemDashboard,
  fetchWorkspaceUnits,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resolveFirstRunState } from "@/lib/first-run-state";
import { useMuldroWs } from "@/hooks/use-muldro-ws";
import { useUnitStore } from "@/stores/unit-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { formatApiError, type ParsedApiError } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import { GreetingHero } from "@/components/dashboard/greeting-hero";
import { BriefingGatheringCard } from "@/components/dashboard/briefing-gathering-card";
import { OnboardingCard } from "@/components/dashboard/onboarding-card";
import { WorkspaceCanvas } from "@/components/workspace/workspace-canvas";
import { WorkspaceStatusBar } from "@/components/workspace/workspace-status-bar";
import { UnitDetail } from "@/components/workspace/unit-detail";
import { PreparedQueue } from "@/components/workspace/prepared-queue";
import type { Unit } from "@/lib/types/unit";

export default function WorkspacePage() {
  const { user } = useAuth();
  const setUnits = useUnitStore((s) => s.setUnits);
  const upsertUnit = useUnitStore((s) => s.upsertUnit);
  const removeUnit = useUnitStore((s) => s.removeUnit);
  const units = useUnitStore((s) => s.units);
  const activeKey = useUnitStore((s) => s.activeKey);
  const detailOpen = useUnitStore((s) => s.detailOpen);
  const openDetail = useUnitStore((s) => s.openDetail);
  const closeDetail = useUnitStore((s) => s.closeDetail);
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

  const { data: runtime } = useQuery({
    queryKey: ["runtime-summary"],
    queryFn: fetchRuntimeSummary,
    refetchInterval: 30_000,
  });

  const { data: unitData } = useQuery({
    queryKey: ["workspace-units"],
    queryFn: fetchWorkspaceUnits,
    refetchInterval: 15_000,
  });

  // The server is the ordering authority: it ranks, and the client renders
  // that order. A REST refresh therefore REPLACES the list rather than merging
  // into it — a client-side merge would reintroduce arrival order, which is
  // exactly what ranking exists to remove. Live pushes land on top via
  // upsertUnit until the next refresh re-ranks.
  useEffect(() => {
    if (unitData?.units) setUnits(unitData.units);
  }, [unitData, setUnits]);

  const handleUnitPush = useCallback((unit: Unit) => upsertUnit(unit), [upsertUnit]);

  const handleDismiss = useCallback(
    (key: string) => {
      removeUnit(key);
      // Demotion only, and fire-and-forget: the card is gone from THIS view
      // either way, and a failed write costs a ranking signal, not the action.
      void dismissUnit(key).catch(() => undefined);
    },
    [removeUnit]
  );

  const { sendAction } = useMuldroWs({
    userId: user?.user_id ?? "",
    onUnitPush: handleUnitPush,
    onError: handleWsError,
    enabled: !!user,
  });

  useEffect(() => {
    setGlobalSendAction(sendAction);
  }, [sendAction, setGlobalSendAction]);

  const active = activeKey ? units.find((u) => u.frame.key === activeKey) ?? null : null;

  const sourceCount = system?.observations ? Object.keys(system.observations).length : 0;

  const briefing = units.find((u) => u.frame.kind === "briefing") ?? null;
  const headline = briefing?.frame.headline ?? null;
  const approvalCount = units.filter((u) => u.frame.status === "needs_you").length;
  // First-load state: onboarding (no source yet), gathering (source connected,
  // briefing pending), or active (briefing exists). See resolveFirstRunState.
  const firstRunState = resolveFirstRunState(sourceCount, Boolean(briefing));

  return (
    <div className="p-4 sm:p-6 space-y-4 animate-fade-in">
      <GreetingHero
        headline={headline}
        approvalCount={approvalCount}
        sourceCount={sourceCount}
      />

      <WorkspaceStatusBar
        system={system}
        activeAgents={runtime?.active_agents ?? []}
      />

      {firstRunState === "onboarding" && <OnboardingCard />}
      {firstRunState === "gathering" && (
        <BriefingGatheringCard sourceCount={sourceCount} />
      )}

      {/* The canvas owns its own empty state. Do not guard on units.length
          here — that made the empty state unreachable outside its own test. */}
      <WorkspaceCanvas
        units={units}
        onOpen={openDetail}
        onDismiss={handleDismiss}
        foldAfter={unitData?.fold_after}
      />

      <UnitDetail
        unit={active}
        open={detailOpen}
        onClose={closeDetail}
        onAct={(capability) => sendAction("capability", { capability, key: active?.frame.key })}
      >
        {active?.frame.entity_type === "prepared_work" && <PreparedQueue />}
      </UnitDetail>
    </div>
  );
}

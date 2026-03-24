"use client";

import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { approveAction, rejectAction } from "@/lib/api";
import type { A2UISurface } from "@/lib/a2ui-types";
import type { Approval } from "@/lib/types";
import type { GeneratedSurface, SurfaceKind } from "@/lib/types/surfaces";

interface Props {
  approvals: Approval[];
  briefingHeadline: string | null;
  recommendedActions: Array<{ title: string; reason: string; action_type?: string }>;
  priorityItems: Array<{ title: string; summary: string; urgency?: string }>;
}

function approvalToSurface(approval: Approval): GeneratedSurface {
  return {
    id: `approval_${approval.approval_id}`,
    kind: "approval",
    title: approval.title || "Pending Approval",
    data: {
      risk_level: approval.risk_level,
      summary: approval.summary,
      approval_id: approval.approval_id,
    },
    created_at: approval.created_at ?? new Date().toISOString(),
    pinned: true,
    position: "workspace",
    schema_version: 1,
    source_message_id: null,
    source_run_id: null,
    source_artifact_id: null,
  };
}

function actionToSurface(action: { title: string; reason: string; action_type?: string }, idx: number): GeneratedSurface {
  return {
    id: `action_${idx}_${Date.now()}`,
    kind: "recommendation" as SurfaceKind,
    title: action.title,
    data: { text: action.reason, highlights: [] },
    created_at: new Date().toISOString(),
    pinned: false,
    position: "workspace",
    schema_version: 1,
    source_message_id: null,
    source_run_id: null,
    source_artifact_id: null,
  };
}

function priorityToSurface(item: { title: string; summary: string; urgency?: string }, idx: number): GeneratedSurface {
  return {
    id: `priority_${idx}_${Date.now()}`,
    kind: "alert",
    title: item.title,
    data: {
      level: item.urgency === "critical" ? "error" : "warning",
      title: item.title,
      message: item.summary,
    },
    created_at: new Date().toISOString(),
    pinned: false,
    position: "workspace",
    schema_version: 1,
    source_message_id: null,
    source_run_id: null,
    source_artifact_id: null,
  };
}

export function WorkspaceCanvas({
  approvals,
  briefingHeadline,
  recommendedActions,
  priorityItems,
}: Props) {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const { surfaces, removeSurface, togglePin } = useSurfaceStore();
  const queryClient = useQueryClient();

  // Merge static data (approvals, recommendations, priorities) with dynamic WebSocket surfaces
  const workspaceSurfaces = useMemo(() => {
    const wsSurfaces = surfaces.filter((s) => s.position === "workspace");

    const approvalSurfaces = approvals.map(approvalToSurface);
    const actionSurfaces = recommendedActions.slice(0, 3).map(actionToSurface);
    const prioritySurfaces = priorityItems.slice(0, 3).map(priorityToSurface);

    // Briefing as a surface
    const briefingSurfaces: GeneratedSurface[] = briefingHeadline
      ? [
          {
            id: "briefing_today",
            kind: "briefing",
            title: "Today's Briefing",
            data: { headline: briefingHeadline },
            created_at: new Date().toISOString(),
            pinned: false,
            position: "workspace",
            schema_version: 1,
            source_message_id: null,
            source_run_id: null,
            source_artifact_id: null,
          },
        ]
      : [];

    // Approvals first, then priorities, then briefing, then actions, then WebSocket surfaces
    return [
      ...approvalSurfaces,
      ...prioritySurfaces,
      ...briefingSurfaces,
      ...actionSurfaces,
      ...wsSurfaces,
    ];
  }, [approvals, briefingHeadline, recommendedActions, priorityItems, surfaces]);

  async function handleApprovalAction(approvalId: string, action: "approve" | "reject") {
    try {
      if (action === "approve") {
        await approveAction(approvalId);
      } else {
        await rejectAction(approvalId);
      }
      queryClient.invalidateQueries({ queryKey: ["workspace-approvals"] });
      queryClient.invalidateQueries({ queryKey: ["system-dashboard"] });
    } catch {
      // Toast handled by caller if needed
    }
  }

  if (workspaceSurfaces.length === 0) {
    return (
      <div className="rounded-xl border border-b-primary bg-surface-0 p-6">
        <div className="flex flex-col items-center text-center max-w-sm mx-auto py-4">
          <div className="w-12 h-12 rounded-full bg-green-500/10 flex items-center justify-center mb-3">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              className="text-green-400"
            >
              <path
                d="M9 12l2 2 4-4"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
            </svg>
          </div>
          <p className="text-sm text-t-primary font-medium">
            Nothing needs your attention
          </p>
          <p className="text-xs text-t-tertiary mt-1 mb-5">
            Jarvis is watching your connected sources. Updates will appear here.
          </p>
          <div className="flex gap-2">
            <Link
              href="/chat"
              className="px-4 py-2 rounded-lg bg-accent-primary text-white text-xs font-medium hover:opacity-90 transition-opacity"
            >
              Talk to Jarvis
            </Link>
            <Link
              href="/integrations"
              className="px-4 py-2 rounded-lg border border-b-primary text-t-secondary text-xs hover:bg-surface-1 transition-colors"
            >
              Connect Sources
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {workspaceSurfaces.map((surface) => {
        const isApproval = surface.kind === "approval" && !!surface.data.approval_id;

        return (
          <div key={surface.id} className="flex flex-col">
            {surface.data?.a2ui_surface ? (
              <A2UIRenderer
                surface={surface.data.a2ui_surface as A2UISurface}
                onAction={(action, payload) =>
                  handleA2UIAction(sendAction, action, payload)
                }
              />
            ) : (
              <div className="rounded-xl border border-dashed border-b-primary bg-surface-1 p-3 text-xs text-t-tertiary">
                Surface unavailable: missing A2UI payload.
              </div>
            )}
            {/* Approval action buttons */}
            {isApproval && (
              <div className="flex gap-2 mt-2">
                <button
                  onClick={() =>
                    handleApprovalAction(
                      surface.data.approval_id as string,
                      "approve"
                    )
                  }
                  className="flex-1 px-3 py-2 rounded-lg bg-green-500/10 text-green-400 text-xs font-medium hover:bg-green-500/20 transition-colors"
                >
                  Approve
                </button>
                <button
                  onClick={() =>
                    handleApprovalAction(
                      surface.data.approval_id as string,
                      "reject"
                    )
                  }
                  className="flex-1 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs font-medium hover:bg-red-500/20 transition-colors"
                >
                  Reject
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

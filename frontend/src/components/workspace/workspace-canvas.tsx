"use client";

import { useMemo } from "react";
import Link from "next/link";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";
import type { Approval } from "@/lib/types";
import type { GeneratedSurface, SurfaceKind } from "@/lib/types/surfaces";

interface Props {
  approvals: Approval[];
  briefingHeadline: string | null;
  recommendedActions: Array<{ title: string; reason: string; action_type?: string }>;
  priorityItems: Array<{ title: string; summary: string; urgency?: string }>;
}

function textComponent(id: string, text: string, variant: "heading" | "body" | "caption" = "body"): A2UIComponent {
  return {
    type: "Text",
    id,
    properties: { text, variant },
    children: [],
    actions: [],
  };
}

function buttonComponent(id: string, label: string, variant: "primary" | "secondary" | "danger", payload: Record<string, unknown>): A2UIComponent {
  return {
    type: "Button",
    id,
    properties: { label, variant },
    children: [],
    actions: [{ type: "click", payload }],
  };
}

function cardSurface(id: string, metadata: Record<string, unknown>, children: A2UIComponent[]): A2UISurface {
  return {
    type: "surface",
    id,
    metadata,
    children: [
      {
        type: "Card",
        id: `${id}_card`,
        properties: {},
        actions: [],
        children,
      },
    ],
  };
}

function approvalToSurface(approval: Approval): GeneratedSurface {
  const id = `approval_${approval.approval_id}`;
  const a2uiSurface = cardSurface(
    id,
    { kind: "approval" },
    [
      textComponent(`${id}_label`, "Approval Required", "caption"),
      textComponent(`${id}_title`, approval.title || "Pending Approval", "heading"),
      textComponent(
        `${id}_summary`,
        approval.summary ?? "Review this request and choose approve or reject.",
        "body"
      ),
      {
        type: "Row",
        id: `${id}_actions`,
        properties: {},
        actions: [],
        children: [
          buttonComponent(`${id}_approve`, "Approve", "primary", {
            action: "approve",
            id: approval.approval_id,
          }),
          buttonComponent(`${id}_reject`, "Reject", "danger", {
            action: "reject",
            id: approval.approval_id,
          }),
        ],
      },
    ]
  );

  return {
    id,
    kind: "approval",
    title: approval.title || "Pending Approval",
    data: {
      risk_level: approval.risk_level,
      summary: approval.summary,
      approval_id: approval.approval_id,
      a2ui_surface: a2uiSurface,
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
  const id = `action_${idx}`;
  const a2uiSurface = cardSurface(
    id,
    { kind: "recommendation" },
    [
      textComponent(`${id}_label`, "Recommended Action", "caption"),
      textComponent(`${id}_title`, action.title, "heading"),
      textComponent(`${id}_reason`, action.reason, "body"),
    ]
  );

  return {
    id,
    kind: "recommendation" as SurfaceKind,
    title: action.title,
    data: { text: action.reason, highlights: [], a2ui_surface: a2uiSurface },
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
  const id = `priority_${idx}`;
  const a2uiSurface = cardSurface(
    id,
    { kind: "alert", urgency: item.urgency ?? "medium" },
    [
      textComponent(`${id}_label`, "Priority", "caption"),
      textComponent(`${id}_title`, item.title, "heading"),
      textComponent(`${id}_summary`, item.summary, "body"),
    ]
  );

  return {
    id,
    kind: "alert",
    title: item.title,
    data: {
      level: item.urgency === "critical" ? "error" : "warning",
      title: item.title,
      message: item.summary,
      a2ui_surface: a2uiSurface,
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
  const { surfaces } = useSurfaceStore();

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
            data: {
              headline: briefingHeadline,
              a2ui_surface: cardSurface(
                "briefing_today",
                { kind: "briefing" },
                [
                  textComponent("briefing_today_label", "Briefing", "caption"),
                  textComponent("briefing_today_title", "Today's Briefing", "heading"),
                  textComponent("briefing_today_headline", briefingHeadline, "body"),
                ]
              ),
            },
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
        const a2uiSurface = surface.data?.a2ui_surface as A2UISurface | undefined;
        if (!a2uiSurface) {
          return null;
        }

        return (
          <div key={surface.id} className="flex flex-col">
            <A2UIRenderer
              surface={a2uiSurface}
              onAction={(action, payload) =>
                handleA2UIAction(sendAction, action, payload)
              }
            />
          </div>
        );
      })}
    </div>
  );
}

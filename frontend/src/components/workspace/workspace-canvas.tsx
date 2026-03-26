"use client";

import Link from "next/link";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { ErrorBoundary } from "@/components/error-boundary";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { A2UISurface } from "@/lib/a2ui-types";

interface Props {
  surfaces: A2UISurface[];
}

export function WorkspaceCanvas({ surfaces }: Props) {
  const sendAction = useWsActionStore((s) => s.sendAction);

  if (surfaces.length === 0) {
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
      {surfaces.map((surface) => (
        <ErrorBoundary
          key={`seb-${surface.id}`}
          fallback={
            <div className="rounded-lg border border-red-500/30 bg-surface-1 p-4">
              <p className="text-sm text-t-secondary">Surface failed to load</p>
              <p className="text-xs text-t-tertiary mt-1">ID: {surface.id}</p>
            </div>
          }
        >
          <div className="flex flex-col">
            <A2UIRenderer
              surface={surface}
              onAction={(action, payload) =>
                handleA2UIAction(sendAction, action, payload)
              }
            />
          </div>
        </ErrorBoundary>
      ))}
    </div>
  );
}

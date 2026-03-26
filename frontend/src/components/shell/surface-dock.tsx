"use client";

import { useMemo } from "react";
import { useSurfaceStore } from "@/stores/surface-store";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { A2UISurface } from "@/lib/a2ui-types";

export function SurfaceDock() {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const allSurfaces = useSurfaceStore((s) => s.surfaces);
  const surfaces = useMemo(() => allSurfaces.filter((sf) => sf.position === "right-pane"), [allSurfaces]);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const togglePin = useSurfaceStore((s) => s.togglePin);
  const setPosition = useSurfaceStore((s) => s.setPosition);

  if (surfaces.length === 0) return null;

  return (
    <aside className="w-80 border-l border-b-primary bg-surface-0 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-b-primary flex items-center justify-between shrink-0">
        <span className="text-xs font-medium text-t-secondary">
          Surfaces ({surfaces.length})
        </span>
      </div>

      {/* Surface cards */}
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {surfaces.map((surface) => (
          <div key={surface.id} className="relative group">
            {surface.data?.a2ui_surface ? (
              <A2UIRenderer
                surface={surface.data.a2ui_surface as A2UISurface}
                onAction={(action, payload) =>
                  handleA2UIAction(sendAction, action, payload)
                }
              />
            ) : (
              <div className="rounded-xl border border-dashed border-b-primary bg-surface-1 p-2 text-[11px] text-t-tertiary">
                Missing A2UI payload.
              </div>
            )}
            {/* Dock controls */}
            <div className="absolute top-1 right-12 opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
              <button
                onClick={() => setPosition(surface.id, "inline")}
                title="Move to chat"
                className="p-1 rounded bg-surface-2 text-t-tertiary hover:text-t-primary text-[10px] cursor-pointer"
              >
                ↓
              </button>
              <button
                onClick={() => setPosition(surface.id, "center-pane")}
                title="Expand to center"
                className="p-1 rounded bg-surface-2 text-t-tertiary hover:text-t-primary text-[10px] cursor-pointer"
              >
                ⬜
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

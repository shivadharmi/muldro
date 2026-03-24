"use client";

import { useMemo } from "react";
import { useSurfaceStore } from "@/stores/surface-store";
import { A2UIRenderer } from "@/components/a2ui/renderer";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { A2UISurface } from "@/lib/a2ui-types";

export function CenterPaneSurface() {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const allSurfaces = useSurfaceStore((s) => s.surfaces);
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const setActiveSurface = useSurfaceStore((s) => s.setActiveSurface);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const togglePin = useSurfaceStore((s) => s.togglePin);
  const setPosition = useSurfaceStore((s) => s.setPosition);

  const centerSurfaces = useMemo(
    () => allSurfaces.filter((sf) => sf.position === "center-pane"),
    [allSurfaces]
  );

  // Show active surface, or the most recent center-pane surface
  const surface = activeSurfaceId
    ? centerSurfaces.find((s) => s.id === activeSurfaceId)
    : centerSurfaces[centerSurfaces.length - 1];

  if (!surface) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 z-40 backdrop-blur-sm"
        onClick={() => {
          setActiveSurface(null);
          setPosition(surface.id, "right-pane");
        }}
      />

      {/* Center panel */}
      <div className="fixed inset-8 sm:inset-12 lg:inset-x-24 lg:inset-y-12 z-50 flex flex-col">
        <div className="flex-1 rounded-xl border border-b-primary bg-surface-0 shadow-lg overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-b-primary shrink-0">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-t-primary">{surface.title}</h2>
              <span className="text-[10px] text-t-tertiary px-1.5 py-0.5 rounded bg-surface-2 capitalize">
                {surface.kind}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPosition(surface.id, "right-pane")}
                title="Dock to side"
                className="p-1.5 rounded-md text-t-tertiary hover:text-t-primary hover:bg-surface-1 transition-colors cursor-pointer text-xs"
              >
                &#8614;
              </button>
              <button
                onClick={() => setPosition(surface.id, "inline")}
                title="Move to chat"
                className="p-1.5 rounded-md text-t-tertiary hover:text-t-primary hover:bg-surface-1 transition-colors cursor-pointer text-xs"
              >
                &#8615;
              </button>
              <button
                onClick={() => togglePin(surface.id)}
                title={surface.pinned ? "Unpin" : "Pin"}
                className={`p-1.5 rounded-md transition-colors cursor-pointer text-xs ${
                  surface.pinned ? "text-accent-primary" : "text-t-tertiary hover:text-t-primary"
                }`}
              >
                &#9733;
              </button>
              <button
                onClick={() => {
                  removeSurface(surface.id);
                  setActiveSurface(null);
                }}
                title="Close"
                className="p-1.5 rounded-md text-t-tertiary hover:text-red-400 hover:bg-surface-1 transition-colors cursor-pointer text-xs"
              >
                &#10005;
              </button>
            </div>
          </div>

          {/* Surface content */}
          <div className="flex-1 overflow-y-auto p-4">
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
          </div>

          {/* Tab bar for multiple center-pane surfaces */}
          {centerSurfaces.length > 1 && (
            <div className="flex items-center gap-1 px-3 py-2 border-t border-b-primary shrink-0 overflow-x-auto">
              {centerSurfaces.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setActiveSurface(s.id)}
                  className={`px-2.5 py-1 text-[10px] rounded-md whitespace-nowrap cursor-pointer transition-colors ${
                    s.id === surface.id
                      ? "bg-accent-primary/10 text-accent-primary"
                      : "text-t-tertiary hover:text-t-secondary hover:bg-surface-1"
                  }`}
                >
                  {s.title}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

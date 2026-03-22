"use client";

import { useMemo } from "react";
import { useSurfaceStore } from "@/stores/surface-store";
import { GeneratedSurfaceCard } from "@/components/primitives/generated-surface";

export function SurfaceDock() {
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
            <GeneratedSurfaceCard
              surface={surface}
              onPin={togglePin}
              onRemove={removeSurface}
            />
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

/** Surface state: workspace surfaces with rich preview + detail modal. */

import { create } from "zustand";

import type { DetailConfig, SurfacePreview } from "@/lib/a2ui-types";
import type { A2UIComponent } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export interface WorkspaceSurface {
  id: string;
  kind: SurfaceKind;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  decision: string | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  children?: A2UIComponent[];
}

interface SurfaceState {
  surfaces: WorkspaceSurface[];
  activeSurfaceId: string | null;
  detailModalOpen: boolean;

  addSurface: (surface: WorkspaceSurface) => void;
  removeSurface: (id: string) => void;
  setActiveSurface: (id: string | null) => void;
  openDetailModal: (id: string) => void;
  closeDetailModal: () => void;
  setSurfaces: (surfaces: WorkspaceSurface[]) => void;
}

export const useSurfaceStore = create<SurfaceState>((set) => ({
  surfaces: [],
  activeSurfaceId: null,
  detailModalOpen: false,

  addSurface: (surface) =>
    set((s) => {
      if (!surface?.id || !surface.kind) return s;
      const idx = s.surfaces.findIndex((sf) => sf.id === surface.id);
      if (idx === -1) {
        return { surfaces: [...s.surfaces, surface] };
      }
      const next = [...s.surfaces];
      next[idx] = { ...next[idx], ...surface };
      return { surfaces: next };
    }),

  removeSurface: (id) =>
    set((s) => ({
      surfaces: s.surfaces.filter((sf) => sf.id !== id),
      activeSurfaceId: s.activeSurfaceId === id ? null : s.activeSurfaceId,
      detailModalOpen: s.activeSurfaceId === id ? false : s.detailModalOpen,
    })),

  setActiveSurface: (id) => set({ activeSurfaceId: id }),

  openDetailModal: (id) =>
    set({ activeSurfaceId: id, detailModalOpen: true }),

  closeDetailModal: () =>
    set({ detailModalOpen: false }),

  setSurfaces: (surfaces) => set({ surfaces }),
}));

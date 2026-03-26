/** Surface state: active and pinned generated surfaces from A2UI. */

import { create } from "zustand";

import type { GeneratedSurface, SurfacePosition } from "@/lib/types/surfaces";

interface SurfaceState {
  surfaces: GeneratedSurface[];
  activeSurfaceId: string | null;

  addSurface: (surface: GeneratedSurface) => void;
  removeSurface: (id: string) => void;
  togglePin: (id: string) => void;
  setPosition: (id: string, position: SurfacePosition) => void;
  setActiveSurface: (id: string | null) => void;
  clearUnpinned: () => void;
}

export const useSurfaceStore = create<SurfaceState>((set) => ({
  surfaces: [],
  activeSurfaceId: null,

  addSurface: (surface) =>
    set((s) => {
      // Validate before storing — reject malformed surfaces
      if (!surface?.id) {
        console.warn("[surface-store] Rejected surface with missing id");
        return s;
      }
      if (!surface.kind || !surface.data) {
        console.warn("[surface-store] Rejected surface with missing kind/data:", surface.id);
        return s;
      }

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
    })),

  togglePin: (id) =>
    set((s) => ({
      surfaces: s.surfaces.map((sf) =>
        sf.id === id ? { ...sf, pinned: !sf.pinned } : sf
      ),
    })),

  setPosition: (id, position) =>
    set((s) => ({
      surfaces: s.surfaces.map((sf) =>
        sf.id === id ? { ...sf, position } : sf
      ),
    })),

  setActiveSurface: (id) => set({ activeSurfaceId: id }),

  clearUnpinned: () =>
    set((s) => ({ surfaces: s.surfaces.filter((sf) => sf.pinned) })),
}));

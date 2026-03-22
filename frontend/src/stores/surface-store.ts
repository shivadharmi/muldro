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
    set((s) => ({ surfaces: [...s.surfaces, surface] })),

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

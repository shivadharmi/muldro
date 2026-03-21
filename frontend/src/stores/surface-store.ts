/** Surface state: active and pinned generated surfaces from A2UI. */

import { create } from "zustand";

import type { GeneratedSurface } from "@/lib/types/surfaces";

interface SurfaceState {
  surfaces: GeneratedSurface[];
  addSurface: (surface: GeneratedSurface) => void;
  removeSurface: (id: string) => void;
  togglePin: (id: string) => void;
  clearUnpinned: () => void;
}

export const useSurfaceStore = create<SurfaceState>((set) => ({
  surfaces: [],

  addSurface: (surface) =>
    set((s) => ({ surfaces: [...s.surfaces, surface] })),

  removeSurface: (id) =>
    set((s) => ({ surfaces: s.surfaces.filter((sf) => sf.id !== id) })),

  togglePin: (id) =>
    set((s) => ({
      surfaces: s.surfaces.map((sf) =>
        sf.id === id ? { ...sf, pinned: !sf.pinned } : sf
      ),
    })),

  clearUnpinned: () =>
    set((s) => ({ surfaces: s.surfaces.filter((sf) => sf.pinned) })),
}));

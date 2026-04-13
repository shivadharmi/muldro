/** Surface state: workspace surfaces with rich preview + detail modal. */

import { create } from "zustand";

import type { DetailConfig, SurfacePreview } from "@/lib/a2ui-types";
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary, SurfaceUpdate } from "@/lib/a2ui-types";
import type { SurfaceKind } from "@/lib/types/surfaces";

export interface WorkspaceSurface {
  id: string;
  kind: SurfaceKind;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  // Execution surface fields (populated by surface_update messages)
  phase?: ExecutionPhase;
  steps?: StepState[];
  current_step?: string | null;
  progress?: string;
  approval?: ApprovalContext | null;
  results?: ResultSummary | null;
  // Insight surface fields
  insight_data?: Record<string, unknown> | null;
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
  updateSurface: (surfaceId: string, update: SurfaceUpdate) => void;
  transitionToExecution: (surfaceId: string) => void;
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

  setSurfaces: (surfaces) =>
    set({ surfaces: surfaces.slice(0, 20) }),

  updateSurface: (surfaceId, update) =>
    set((s) => {
      const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
      if (idx === -1) return s;
      const prev = s.surfaces[idx];
      const next = [...s.surfaces];
      next[idx] = {
        ...prev,
        ...(update.phase !== undefined && { phase: update.phase }),
        ...(update.steps && update.steps.length > 0 && { steps: update.steps }),
        ...(update.current_step !== undefined && { current_step: update.current_step }),
        ...(update.progress !== undefined && { progress: update.progress }),
        ...(update.approval !== undefined && { approval: update.approval }),
        ...(update.results !== undefined && { results: update.results }),
      };
      return { surfaces: next };
    }),

  transitionToExecution: (surfaceId) =>
    set((s) => {
      const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
      if (idx === -1) return s;
      const next = [...s.surfaces];
      next[idx] = {
        ...next[idx],
        phase: "planning",
        insight_data: null,
      };
      return { surfaces: next };
    }),
}));

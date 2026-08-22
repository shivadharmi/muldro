/** Surface state: workspace surfaces with rich preview + detail modal. */

import { create } from "zustand";

import type { DetailConfig, InsightData, SurfaceDataPayload, SurfaceKind, SurfacePreview } from "@/lib/a2ui-types";
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary, SurfaceUpdate } from "@/lib/types/execution";

export interface WorkspaceSurface {
  id: string;
  kind: SurfaceKind;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  // Execution surface fields (populated by surface_update messages)
  phase?: ExecutionPhase | null;
  steps?: StepState[] | null;
  current_step?: string | null;
  progress?: string | null;
  approval?: ApprovalContext | null;
  results?: ResultSummary | null;
  // Insight surface fields
  insight_data?: InsightData | null;
  // Trust context metadata for approval surfaces.
  trust_context?: Record<string, string> | null;
  // Presenter-authored typed rich content (renders via A2UIRenderer).
  surface_data?: SurfaceDataPayload | null;
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

  // Closing DROPS the active surface, so the detail modal unmounts and its per-open
  // `tabCache` goes with it. Leaving `activeSurfaceId` set kept the modal mounted and its
  // fetched detail tabs cached indefinitely — the cache only reset on a surface *id* change,
  // which never happens for a standing singleton like `prepared_work_{workspace_id}`. The
  // founder would approve one queued item, close, reopen, and see the approved row still
  // listed with a live Approve button (the API 409s, so it is a stale view rather than a
  // double-execute — but the review surface must not report decided work as outstanding).
  // The cache's purpose is switching tabs within one open session; it should not survive it.
  closeDetailModal: () =>
    set({ detailModalOpen: false, activeSurfaceId: null }),

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
        ...(update.steps !== undefined && { steps: update.steps }),
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

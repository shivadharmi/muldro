/** History state: paginated run list with live execution updates. */

import { create } from "zustand";

import type { ApprovalContext } from "@/lib/a2ui-types";

// ── TypeScript interfaces ────────────────────────────────────────

export interface HistoryStepSummary {
  step_id: string | null;
  name: string | null;
  capability: string | null;
  status: string | null;
  started_at: string | null;
  completed_at: string | null;
}

/** Thin approval subset — the byte-neutral fallback the REST/history path returns
 *  when the run has no persisted rich {@link ApprovalContext}. */
export interface HistoryApprovalContext {
  approval_id: string | null;
  step_id: string | null;
  step_description: string | null;
  risk_level: string | null;
  trust_level: string | null;
}

/** Either the rich unified {@link ApprovalContext} (when the run's persisted surface
 *  carries one) or the thin fallback. The frontend renders the unified
 *  `InlineApprovalCard` for the rich arm and a legacy fallback for the thin arm. */
export type RunApproval = ApprovalContext | HistoryApprovalContext;

export interface HistoryItem {
  run_id: string;
  plan_id: string | null;
  goal: string | null;
  source: string | null;
  trigger_type: string | null;
  status: string;
  risk_level: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: Record<string, unknown> | null;
  retry_count: number;
  step_count: number;
  completed_step_count: number;
  cost_usd: number | null;
  // Primary agent that handled the run, when attributable.
  agent?: string | null;
  // Total run duration in milliseconds.
  duration_ms?: number | null;
  // Last-updated timestamp (distinct from started/completed).
  updated_at?: string | null;
  steps: HistoryStepSummary[];
  approval: RunApproval | null;
  live_phase: string | null;
  surface_id: string | null;
}

export interface HistoryFilters {
  status: string;
  source: string;
  search: string;
  dateFrom: string | null;
  dateTo: string | null;
}

// ── Live update shape (from WS surface_update messages) ─────────

interface LiveRunUpdate {
  phase?: string;
  steps?: HistoryStepSummary[];
  approval?: RunApproval | null;
  status?: string;
}

// ── Store state & actions ────────────────────────────────────────

interface HistoryState {
  items: HistoryItem[];
  total: number;
  offset: number;
  filters: HistoryFilters;
  surfaceToRunMap: Record<string, string>;
  detailRunId: string | null;
  detailModalOpen: boolean;

  setItems: (items: HistoryItem[], total: number) => void;
  appendItems: (newItems: HistoryItem[], total: number) => void;
  setFilters: (partial: Partial<HistoryFilters>) => void;
  setOffset: (offset: number) => void;
  updateRunLiveState: (surfaceId: string, update: LiveRunUpdate) => void;
  openDetail: (runId: string) => void;
  closeDetail: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────

function buildSurfaceMap(items: HistoryItem[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const item of items) {
    if (item.surface_id) {
      map[item.surface_id] = item.run_id;
    }
  }
  return map;
}

function deriveStatus(phase: string | undefined, existing: string): string {
  if (phase === "completed") return "completed";
  if (phase === "failed") return "failed";
  if (phase === "approval_needed") return "awaiting_approval";
  return existing;
}

// ── Store ────────────────────────────────────────────────────────

export const useHistoryStore = create<HistoryState>((set) => ({
  items: [],
  total: 0,
  offset: 0,
  filters: {
    status: "all",
    source: "all",
    search: "",
    dateFrom: null,
    dateTo: null,
  },
  surfaceToRunMap: {},
  detailRunId: null,
  detailModalOpen: false,

  setItems: (items, total) =>
    set({
      items,
      total,
      surfaceToRunMap: buildSurfaceMap(items),
    }),

  appendItems: (newItems, total) =>
    set((s) => {
      const combined = [...s.items, ...newItems];
      return {
        items: combined,
        total,
        surfaceToRunMap: { ...s.surfaceToRunMap, ...buildSurfaceMap(newItems) },
      };
    }),

  setFilters: (partial) =>
    set((s) => ({
      filters: { ...s.filters, ...partial },
      offset: 0,
    })),

  setOffset: (offset) => set({ offset }),

  updateRunLiveState: (surfaceId, update) =>
    set((s) => {
      const runId = s.surfaceToRunMap[surfaceId];
      if (!runId) return s;

      const idx = s.items.findIndex((item) => item.run_id === runId);
      if (idx === -1) return s;

      const prev = s.items[idx];
      const nextItems = [...s.items];
      nextItems[idx] = {
        ...prev,
        ...(update.phase !== undefined && { live_phase: update.phase }),
        ...(update.steps && update.steps.length > 0 && { steps: update.steps }),
        ...(update.approval !== undefined && { approval: update.approval }),
        status: deriveStatus(update.phase, prev.status),
      };
      return { items: nextItems };
    }),

  openDetail: (runId) =>
    set({ detailRunId: runId, detailModalOpen: true }),

  closeDetail: () =>
    set({ detailRunId: null, detailModalOpen: false }),
}));

/** History state: paginated run list with live execution updates. */

import { create } from "zustand";

// ── TypeScript interfaces ────────────────────────────────────────

export interface HistoryStepSummary {
  step_id: string | null;
  name: string | null;
  capability: string | null;
  status: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface HistoryApprovalContext {
  approval_id: string | null;
  step_id: string | null;
  step_description: string | null;
  risk_level: string | null;
  trust_level: string | null;
}

export interface HistoryItem {
  run_id: string;
  status: string;
  source: string | null;
  intent: string | null;
  capability_summary: string | null;
  total_steps: number | null;
  completed_steps: number | null;
  failed_steps: number | null;
  total_cost_usd: number | null;
  total_tokens: number | null;
  started_at: string | null;
  completed_at: string | null;
  elapsed_seconds: number | null;
  surface_id: string | null;
  phase: string | null;
  steps: HistoryStepSummary[];
  approval: HistoryApprovalContext | null;
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
  approval?: HistoryApprovalContext | null;
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
        ...(update.phase !== undefined && { phase: update.phase }),
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

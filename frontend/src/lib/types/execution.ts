/**
 * The execution and WebSocket vocabulary. Survives the A2UI deletion.
 *
 * These are not view-layer types. `SurfaceUpdate` and its `phase` machine are
 * the autonomous path's live run state — emitted from the graph executor, the
 * DAG runner and the trust gate; the deep chat path emits none — and the
 * history page renders them. None of them belong to the view layer's unit
 * vocabulary, so none of them die with the A2UI component tree.
 *
 * `surface_id` keeps its name because it is a wire field the backend still
 * publishes, and the backend calls it that. Renaming it here would leave the
 * two sides disagreeing about the same message.
 */

import type { Unit } from "@/lib/types/unit";

// ── Action result ──────────────────────────────────────────────

export interface ActionResult {
  action: string;
  status: "success" | "error";
  result?: Record<string, unknown>;
  /** Client-safe error message (from the standardized envelope). */
  message?: string;
  code?: string;
  correlationId?: string | null;
}

// ── Execution surface types ───────────────────────────────────

export type ExecutionPhase =
  | "planning"
  | "plan_ready"
  | "executing"
  | "approval_needed"
  | "completed"
  | "failed"
  | "partial";

// Mirrors backend execution_state.TERMINAL_SUCCESS: a step counts as "done" for
// progress/grouping whether it is confirmed (completed) or fired-but-unconfirmed
// (completed_unverified). partially_completed (read-back diverged) is NOT done.
export const STEP_TERMINAL_SUCCESS = ["completed", "completed_unverified"] as const;

export function isStepDone(status: string): boolean {
  return (STEP_TERMINAL_SUCCESS as readonly string[]).includes(status);
}

export interface StepState {
  step_id: string;
  description: string;
  status:
    | "pending"
    | "executing"
    | "completed"
    | "completed_unverified"
    | "partially_completed"
    | "failed"
    | "approval_needed"
    | "user_action";
  output_summary: string | null;
  duration_ms: number | null;
  started_at: string | null;

  // Evidence
  completed_at: string | null;
  timeout_seconds: number | null;
  error: Record<string, unknown> | null;
  retry_count: number | null;
}

export interface ApprovalContext {
  approval_id: string;
  step_description: string;
  risk_level: string;
  trust_level: string;
  expires_at: string | null;
  triggering_step_id: string | null;
  graduation_hint: string;

  // Evidence
  risk_reasoning: string;
  trust_context: string;
  reversible: boolean;
  blast_radius: string;
  effective_trust_level: string;
  approved_count: number;
  rejected_count: number;
}

export interface ResultSummary {
  key_findings: string[];
  artifacts_created: string[];
  suggested_next: string[];
}

export interface SurfaceUpdate {
  surface_id: string;
  phase: ExecutionPhase;
  steps: StepState[];
  current_step: string | null;
  progress: string;
  approval: ApprovalContext | null;
  results: ResultSummary | null;
  // Cumulative cost/usage for the run, streamed with each live frame.
  tokens?: number | null;
  cost_usd?: number | null;
}

/** WebSocket message types from Muldro backend */
export type MuldroMessage =
  | { type: "unit"; key: string; unit: Unit }
  | {
      type: "notification";
      notification_id: string;
      notification_type: string;
      title: string;
      body: string;
      data: Record<string, unknown>;
    }
  | { type: "notification_resolved"; notification_id: string; resolved_on: string }
  | { type: "action_result"; action: string; status: string; result?: Record<string, unknown>; code?: string; message?: string; correlation_id?: string }
  | { type: "surface_update"; surface_id: string; phase: ExecutionPhase; steps: StepState[]; current_step: string | null; progress: string; approval: ApprovalContext | null; results: ResultSummary | null; tokens?: number | null; cost_usd?: number | null }
  | { type: "heartbeat" }
  | { type: "auth_ok" }
  | { type: "auth_error"; message: string }
  // Standardized WS error frame: { status: "error", code, message, correlation_id }
  | { status: "error"; code?: string; message?: string; correlation_id?: string };

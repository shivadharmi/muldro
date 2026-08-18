/** A2UI Protocol TypeScript types — matches backend src/ui/contracts.py */

export interface A2UIAction {
  type: "click" | "submit" | "change";
  payload: Record<string, unknown>;
}

export interface A2UIComponent {
  type: string; // Text, Card, Row, List, Table, Badge, etc.
  id: string;
  properties: Record<string, unknown>;
  children: A2UIComponent[];
  actions: A2UIAction[];
}

export interface A2UISurface {
  type: "surface";
  id: string;
  children: A2UIComponent[];
  metadata: Record<string, unknown>;
}

// ── Rich preview + detail modal types ──────────────────────────

export interface SurfaceMetric {
  label: string;
  value: string;
  variant: "default" | "success" | "warning" | "danger";
}

export interface SurfacePreview {
  title: string;
  subtitle: string | null;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "awaiting_approval"
    | "cancelled"
    | "proposal"
    | null;
  priority: "low" | "medium" | "high" | "critical" | null;
  metrics: SurfaceMetric[];
  entities: string[];
  progress: number | null;
  timestamp: string | null;
  tags: string[];
  // Cost/usage attribution for the run behind this surface.
  tokens?: number | null;
  cost_usd?: number | null;
  // Risk badge for the surface (e.g. alert/approval cards).
  risk?: "low" | "medium" | "high" | "critical" | null;
  // Short status/state flags rendered as chips.
  flags?: string[];
  // Bullet-style preview lines (e.g. key items in a briefing/checklist).
  items?: string[];
  // One-line evidence/why-this-matters string.
  evidence?: string | null;
  // Last-updated timestamp, distinct from creation `timestamp`.
  updated_at?: string | null;
}

export interface DetailTab {
  id: string;
  label: string;
  endpoint: string;
  icon: string | null;
  badge_count: number | null;
}

export interface DetailConfig {
  tabs: DetailTab[];
  default_tab: string | null;
}

export interface DetailSection {
  id: string;
  title: string;
  collapsed: boolean;
  children: A2UIComponent[];
}

export interface DetailTabResponse {
  tab_id: string;
  sections: DetailSection[];
}

/** Presenter-authored rich content for a surface's detail view.
 *  Each section is a full A2UIComponent tree rendered by the A2UIRenderer. */
export interface SurfaceDataPayload {
  sections: A2UIComponent[];
}

/** New two-layer surface push from backend (preview + detail_config + optional surface_data). */
export interface WorkspaceSurfacePush {
  type: "surface";
  id: string;
  kind: import("@/lib/types/surfaces").SurfaceKind;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  ttl_hours: number;
  surface_data: SurfaceDataPayload | null;
  // Payload for proactive_insight surfaces — carries signal summary,
  // relevance reasoning, goals, and suggested actions. When present the
  // frontend dispatches to insight-surface.tsx rather than the generic
  // card. Previously missing from the type: the backend sent this but
  // the WS handler dropped it as an unknown field.
  insight_data: InsightData | null;
  // Live execution fields merged in so the run card can render its
  // inline phase badge and step summary without a separate
  // SurfaceUpdate message.
  phase: ExecutionPhase | null;
  steps: StepState[] | null;
  current_step: string | null;
  progress: string | null;
  approval: ApprovalContext | null;
  results: ResultSummary | null;
  trust_context: Record<string, string> | null;
}

// ── Insight surface types ────────────────────────────────────

export interface SuggestedActionRef {
  description: string;
  capability: string;
  action_input: Record<string, unknown>;
  action_preview: string;
}

export interface InsightData {
  signal_source: string;
  signal_category: string;
  signal_summary: string;
  relevance_score: number;
  relevance_reasoning: string;
  related_goals: string[];
  suggested_actions: SuggestedActionRef[];
  dismiss_available: boolean;
  // One-line supporting evidence for why this insight surfaced.
  evidence?: string | null;
}

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
  | { type: "surface"; surface: WorkspaceSurfacePush }
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

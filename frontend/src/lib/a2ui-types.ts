/** A2UI Protocol TypeScript types — matches backend src/ui/contracts.py */

export interface A2UIAction {
  type: "click" | "submit" | "change";
  payload: Record<string, unknown>;
}

export interface A2UIComponent {
  type: string; // Text, Button, Card, List, Row, Column, TextField, etc.
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
    | null;
  priority: "low" | "medium" | "high" | "critical" | null;
  metrics: SurfaceMetric[];
  entities: string[];
  progress: number | null;
  timestamp: string | null;
  tags: string[];
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

/** New two-layer surface push from backend (preview + detail_config). */
export interface WorkspaceSurfacePush {
  type: "surface";
  id: string;
  kind: string;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  ttl_hours: number;
}

// ── Action result ──────────────────────────────────────────────

export interface ActionResult {
  action: string;
  status: "success" | "error";
  result?: Record<string, unknown>;
  error?: string;
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

export interface StepState {
  step_id: string;
  description: string;
  status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
  output_summary: string | null;
  duration_ms: number | null;
}

export interface ApprovalContext {
  approval_id: string;
  step_description: string;
  risk_reasoning: string;
  trust_context: string;
  graduation_hint: string;
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
}

/** WebSocket message types from Jarvis backend */
export type JarvisMessage =
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
  | { type: "action_result"; action: string; status: string; result?: Record<string, unknown>; error?: string }
  | { type: "surface_update"; surface_id: string; phase: string; steps: StepState[]; current_step: string | null; progress: string; approval: ApprovalContext | null; results: ResultSummary | null }
  | { type: "heartbeat" }
  | { type: "auth_ok" }
  | { type: "auth_error"; message: string };

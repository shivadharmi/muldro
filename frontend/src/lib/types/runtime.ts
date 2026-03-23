/** Runtime projection types — mirrors backend RuntimeProjectionService output. */

export interface RuntimeStep {
  step_id: string;
  status: string;
  action: string | null;
  tool_name: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface RuntimeRun {
  run_id: string;
  plan_id: string | null;
  status: string;
  created_at: string | null;
  total_steps: number;
  completed_steps: number;
  progress_pct: number;
  blocking_step_id: string | null;
  blocking_reason: string | null;
}

export interface RuntimeEvent {
  event_id: string;
  run_id: string | null;
  step_id: string | null;
  event_type: RuntimeEventType;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export type RuntimeEventType =
  | "command_received"
  | "route_selected"
  | "plan_created"
  | "run_created"
  | "agent_started"
  | "agent_completed"
  | "step_started"
  | "step_completed"
  | "step_failed"
  | "tool_call_started"
  | "tool_call_completed"
  | "tool_call_failed"
  | "approval_requested"
  | "approval_resolved"
  | "artifact_created"
  | "surface_created"
  | "fallback_triggered"
  | "run_completed"
  | "run_failed"
  | "run_cancelled";

export interface AgentWorkload {
  agent_name: string;
  call_count_24h: number;
  avg_duration_ms: number;
}

export interface RuntimeSummary {
  active_runs: number;
  blocked_runs: number;
  completed_24h: number;
  failed_24h: number;
  agents_active: number;
  top_agents: AgentWorkload[];
}

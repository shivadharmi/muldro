/** TypeScript interfaces mirroring backend Pydantic schemas. */

// ── System Dashboard ────────────────────────────────────────────

export interface BudgetInfo {
  daily_spend_usd: number;
  daily_limit_usd: number;
  percent_used: number;
  budget_mode: "normal" | "degraded" | "paused" | "unknown";
}

export interface QueueInfo {
  dlq_pending: number;
  approvals_pending: number;
  plans_in_flight: number;
}

export interface ObservationSourceInfo {
  last_observed_at: string | null;
  status: string;
  items_found: number;
  items_ingested: number;
}

export interface AgentUsageInfo {
  calls_today: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
}

export interface SystemDashboard {
  status: string;
  budget: BudgetInfo;
  queues: QueueInfo;
  observations: Record<string, ObservationSourceInfo>;
  agents: Record<string, AgentUsageInfo>;
}

// ── Canvas Dashboard ────────────────────────────────────────────

export interface DashboardApproval {
  approval_id: string;
  title: string;
  summary: string | null;
  risk_level: string;
  approval_type: string;
  created_at: string | null;
}

export interface DashboardTask {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  decision: string;
  step_count: number;
  steps_completed: number;
  created_at: string | null;
}

export interface DashboardMeeting {
  event_id: string;
  title: string;
  starts_at: string | null;
  attendee_count: number;
  location: string | null;
}

export interface CanvasDashboard {
  headline: string | null;
  date: string;
  pending_approvals: DashboardApproval[];
  active_tasks: DashboardTask[];
  upcoming_meetings: DashboardMeeting[];
  recommended_actions: string[];
  briefing_id: string | null;
}

// ── Approvals ───────────────────────────────────────────────────

export interface Approval {
  approval_id: string;
  status: string;
  title: string;
  summary: string | null;
  risk_level: string;
  created_at: string | null;
}

export interface ApprovalDetail {
  approval_id: string;
  status: string;
  title: string;
  summary: string | null;
  approval_type: string;
  risk_level: string;
  created_at: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  execution_id: string | null;
  plan_goal: string | null;
  artifact_refs: Record<string, unknown> | null;
}

// ── Tasks ───────────────────────────────────────────────────────

export interface Task {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  decision: string;
  created_at: string | null;
}

export interface TaskStep {
  task_id: string;
  task_type: string;
  status: string;
  result_summary: string | null;
}

export interface TaskDetail {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  decision: string;
  risk_level: string;
  reasoning_summary: string | null;
  steps: TaskStep[];
  execution_status: string | null;
  created_at: string | null;
}

// ── Schedules ───────────────────────────────────────────────────

export interface Schedule {
  schedule_id: string;
  user_id: string;
  name: string;
  description: string | null;
  schedule_type: string;
  cron_expr: string | null;
  run_at: string | null;
  action_type: string;
  action_config: Record<string, unknown> | null;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  consecutive_failures: number;
  last_error: string | null;
  source: string;
  priority: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ScheduleCreateInput {
  name: string;
  description?: string;
  schedule_type: string;
  cron_expr?: string;
  run_at?: string;
  action_type: string;
  action_config?: Record<string, unknown>;
  enabled?: boolean;
  priority?: string;
}

export interface ScheduleUpdateInput {
  name?: string;
  description?: string;
  cron_expr?: string;
  run_at?: string;
  action_type?: string;
  action_config?: Record<string, unknown>;
  enabled?: boolean;
  priority?: string;
}

// ── Briefings ───────────────────────────────────────────────────

export interface Briefing {
  briefing_id: string;
  date: string;
  headline: string | null;
  top_priorities: Record<string, unknown>[];
  changes_since_last: Record<string, unknown>[];
  pending_approvals: Record<string, unknown>[];
  recommended_actions: string[];
  full_text: string | null;
}

export interface BriefingFeedbackInput {
  feedback_type: string;
  rating?: number;
  item_section?: string;
  item_index?: number;
  item_title?: string;
  comment?: string;
}

export interface BriefingFeedbackSummary {
  briefing_id: string;
  total_feedback: number;
  average_rating: number | null;
  items_acted_on: number;
  items_dismissed: number;
  follow_ups_asked: number;
}

// ── Search ──────────────────────────────────────────────────────

export interface SearchResult {
  type: string;
  id: string;
  title: string;
  summary: string | null;
  score: number | null;
}

export interface SearchResponse {
  results: SearchResult[];
}

// ── DLQ ─────────────────────────────────────────────────────────

export interface DLQStats {
  total: number;
  by_status: Record<string, number>;
  by_operation: Record<string, number>;
}

// ── Observation ─────────────────────────────────────────────────

export interface ObservationStatus {
  source: string;
  last_observed_at: string | null;
  items_found: number;
  items_ingested: number;
  status: string;
  error_message: string | null;
  is_stale: boolean;
}

// ── Heartbeat ───────────────────────────────────────────────────

export interface HeartbeatResult {
  expired_memories: number;
  stale_plans_found: number;
  plans_escalated: number;
  expired_approvals: number;
  invalidated_plans: number;
  dlq_retried: number;
  timestamp: string;
}

// ── Command ─────────────────────────────────────────────────────

export interface CommandResponse {
  plan_id: string | null;
  decision: string;
  summary: string;
  pending_approvals: Record<string, unknown>[] | null;
  presentation?: string;
}

// ── Standalone Tasks ────────────────────────────────────────────

export interface StandaloneTask {
  task_id: string;
  title: string;
  description: string | null;
  task_type: string;
  source: string;
  priority: string;
  status: string;
  goal_id: string | null;
  parent_task_id: string | null;
  due_at: string | null;
  assigned_agent: string | null;
  created_at: string | null;
}

export interface StandaloneTaskCreateInput {
  title: string;
  description?: string;
  task_type?: string;
  priority?: string;
  goal_id?: string;
  parent_task_id?: string;
  due_at?: string;
}

// ── Goals ───────────────────────────────────────────────────────

export interface Goal {
  goal_id: string;
  title: string;
  description: string | null;
  status: string;
  progress: number;
  priority: string;
  created_at: string | null;
}

export interface GoalCreateInput {
  title: string;
  description?: string;
  priority?: string;
  target_date?: string;
}

// ── Notifications ───────────────────────────────────────────────

export interface Notification {
  notification_id: string;
  channel: string;
  title: string;
  body: string | null;
  priority_score: number;
  status: string;
  sent_at: string | null;
  read_at: string | null;
  created_at: string | null;
}

// ── Workflows ───────────────────────────────────────────────────

export interface Workflow {
  name: string;
  description: string;
  steps: string[];
}

// ── Artifacts ───────────────────────────────────────────────────

export interface Artifact {
  artifact_id: string;
  artifact_type: string;
  title: string;
  content_preview: string | null;
  created_at: string | null;
}

/** TypeScript interfaces mirroring backend Pydantic schemas. */

// ── Domain Types ────────────────────────────────────────────────

export type EntityType =
  | "person"
  | "organization"
  | "project"
  | "meeting"
  | "goal"
  | "task"
  | "document"
  | "message_thread"
  | "repository"
  | "channel"
  | "product"
  | "investment"
  | "website"
  | "tool"
  | "watcher"
  | "location"
  | "health_record"
  | "hobby"
  | "family_member"
  | "financial_account"
  | "media_item"
  | "recipe"
  | "course"
  | "contact_group";

export type RelationType =
  | "works_on"
  | "related_to"
  | "scheduled_with"
  | "reports_to"
  | "owns"
  | "member_of"
  | "assigned_to"
  | "mentioned_in"
  | "depends_on"
  | "attends"
  | "authored"
  | "invested_in"
  | "blocked_by"
  | "sent_by"
  | "attached_to"
  | "derived_from"
  | "monitors"
  | "lives_at"
  | "prescribed_by"
  | "enrolled_in"
  | "follows"
  | "subscribes_to"
  | "shares_with"
  | "cares_for";

export type MemoryType =
  | "episodic"
  | "semantic"
  | "preference"
  | "relationship"
  | "task_context"
  | "procedural";

export type MemoryScope = "presentation" | "planning" | "general";

export type BriefingStyle = "founder" | "personal" | "academic" | "general";

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
  traces: Record<string, unknown>;
  runs: Record<string, unknown>;
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
  trace_id: string | null;
}

// ── Tasks ───────────────────────────────────────────────────────

export interface Task {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
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
  source_db: string | null;
  why_matched: string | null;
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
  target_date: string | null;
  success_criteria_json: Record<string, unknown> | null;
  task_count: number;
  completed_task_count: number;
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
  step_count: number;
  tags: string[];
}

// ── Runs ────────────────────────────────────────────────────────

export interface RunStep {
  step_id: string;
  task_id: string;
  name: string | null;
  step_type: string | null;
  status: string;
  depends_on: string[] | null;
  input_data: Record<string, unknown> | null;
  output_data: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunDetail {
  run_id: string;
  plan_id: string;
  user_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  error: Record<string, unknown> | null;
  retry_count: number;
  step_count: number;
  steps: RunStep[];
}

// ── Agent Routes ────────────────────────────────────────────────

export interface AgentRoute {
  route_id: string;
  name: string;
  description: string | null;
  decision_type: string;
  agent_pipeline: Record<string, unknown>[];
  conditions: Record<string, unknown> | null;
  priority: number;
  enabled: boolean;
  keywords: string[] | null;
  weight: number;
  created_at: string | null;
  updated_at: string | null;
}

// ── Artifacts ───────────────────────────────────────────────────

export interface Artifact {
  artifact_id: string;
  artifact_type: string;
  title: string;
  content_preview: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  source_ref: string | null;
  entity_links: string[] | null;
  metadata_: Record<string, unknown> | null;
  created_at: string | null;
}

// ── Memories ───────────────────────────────────────────────────

export interface MemoryItem {
  memory_id: string;
  memory_type: string;
  scope: string | null;
  fact_text: string;
  confidence: number;
  status: string;
  last_accessed_at: string | null;
  is_stale: boolean;
  entity_ids: string[];
  access_count: number;
  created_at: string | null;
}

// ── Executions ─────────────────────────────────────────────────

export interface ExecutionItem {
  execution_id: string;
  plan_id: string | null;
  status: string;
  source: string;
  execution_mode: string | null;
  current_step_ids: string[] | null;
  error: Record<string, unknown> | null;
  goal: string | null;
  priority: string | null;
  created_at: string | null;
}

// ── Normalized Events ─────────────────────────────────────────

export interface NormalizedEventSummary {
  event_id: string;
  source: string;
  event_type: string;
  title: string | null;
  summary: string | null;
  occurred_at: string | null;
  status: string;
}

// ── Traces ─────────────────────────────────────────────────────

export interface TraceSummary {
  trace_id: string;
  user_id: string;
  trigger: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_tokens: number;
  total_cache_read_tokens: number;
  total_thinking_tokens: number;
  total_cost_usd: number;
  span_count: number;
  error_count: number;
  agents_invoked: string[];
  tools_called: string[];
  memory_writes: number;
}

export interface SpanRecord {
  span_id: string;
  agent_name: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  thinking_tokens: number;
  cost_usd: number;
  tool_calls: string[];
  error: string | null;
}

export interface TraceDetail extends TraceSummary {
  spans: SpanRecord[];
}

export interface AgentPerformance {
  call_count: number;
  total_duration_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_tokens: number;
  total_cache_read_tokens: number;
  total_thinking_tokens: number;
  total_cost_usd: number;
  error_count: number;
  avg_duration_ms: number;
}

export interface AggregateMetrics {
  total_traces: number;
  completed: number;
  failed: number;
  success_rate: number;
  failure_rate: number;
  avg_duration_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  total_errors: number;
  total_memory_writes: number;
  time_range_hours: number;
}

// ── Meeting Prep ───────────────────────────────────────────────

export interface MeetingPrep {
  meeting_id: string;
  title: string;
  starts_at: string | null;
  attendees: Record<string, unknown>[];
  agenda: string[];
  related_threads: Record<string, unknown>[];
  action_items: Record<string, unknown>[];
  risks: string[];
}

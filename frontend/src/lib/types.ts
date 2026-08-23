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
  approval_type: string;
  risk_level: string;
  created_at: string | null;
  /**
   * Which surface can DECIDE this row: "queue" or "chat". A chat approval
   * resumes a suspended turn via /v1/muldro/chat/resume and is 409'd by the
   * decide endpoints on purpose. The client cannot derive this — `artifact_refs`
   * is on the detail response, not here.
   */
  decision_route?: string;
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

// ── Trust ─────────────────────────────────────────────────────

export interface GraduationProgress {
  next_level: string | null;
  current: number;
  target: number;
  percentage: number;
  blocked_by_rejections: boolean;
}

export interface TrustRiskLevel {
  risk_level: string;
  trust_level: string;
  approved_count: number;
  rejected_count: number;
  graduation_progress: GraduationProgress;
}

export interface TrustDashboardEntry {
  capability: string;
  family: string;
  trust_level: string;
  ceiling: string;
  risk_levels: TrustRiskLevel[];
}

export interface TrustCapabilityDetailRisk {
  risk_level: string;
  trust_level: string;
  approved_count: number;
  rejected_count: number;
  modified_count: number;
  last_decision_at: string | null;
  cooldown_until: string | null;
  graduation_progress: GraduationProgress;
}

export interface TrustCapabilityDetail {
  capability: string;
  family: string;
  ceiling: string;
  risk_levels: TrustCapabilityDetailRisk[];
}

export interface TimePolicyRule {
  start_hour: number;
  end_hour: number;
  max_level: string;
  days?: number[] | null;
}

// ── Plan Output ──────────────────────────────────────────────────

export interface PlanStep {
  step_id: string;
  description: string;
  actor: "muldro" | "user";
  capability: string;
  input: Record<string, unknown>;
  depends_on: string[];
  risk: "none" | "low" | "medium" | "high";
  user_context: string | null;
}

export interface CapabilityGap {
  description: string;
  resolution: string;
  workaround: string | null;
}

export interface PlanOutput {
  goal: string;
  reasoning: string;
  achievable: "full" | "partial" | "not_achievable";
  priority: "low" | "medium" | "high" | "critical";
  steps: PlanStep[];
  success_criteria: string;
  capability_gaps: CapabilityGap[];
  plan_id: string | null;
  requires_user_input: boolean;
}

// ── Model Configuration ──────────────────────────────────────────

export interface CatalogModel {
  provider: string;
  model_id: string;
  display_name: string;
  thinking_style: string;
  accepts_temperature: boolean;
  suggested_tier: string;
  context_window: number;
  input_cost_per_1k: number;
  output_cost_per_1k: number;
  supports_prompt_cache: boolean;
}

export interface CredentialFieldSpec {
  key: string;
  label: string;
  kind: "secret" | "text" | "url";
  required: boolean;
  placeholder: string | null;
}

export interface CatalogProvider {
  provider: string;
  display_name: string;
  auth_kind: "api_key" | "keyless_base_url" | "aws_sigv4" | "azure_deployment";
  credential_fields: CredentialFieldSpec[];
  model_count: number;
  docs_url: string | null;
}

export interface AgentInfo {
  name: string;
  display_name: string;
  tier: string;
}

export interface ModelCatalog {
  providers: CatalogProvider[];
  models: CatalogModel[];
  agents: AgentInfo[];
}

/** One model binding. `scope_key` is a tier name ("balanced") or an agent name. */
export interface ModelBinding {
  scope_type: "tier" | "agent";
  scope_key: string;
  provider: string;
  model_id: string;
  effort: "none" | "low" | "medium" | "high";
  max_tokens: number;
  temperature: number | null;
}

export interface ConfigWarning {
  scope_type: "tier" | "agent";
  scope_key: string;
  /** The provider that could not be resolved. Lets a client group warnings by
   *  provider, and lets a revoke report only the bindings IT broke. */
  provider: string;
  code: "provider_not_configured";
  message: string;
}

/** Partial credential update — the body of a provider credential save.
 *  `JSON.stringify` drops `undefined`, which is what makes omission expressible:
 *  an omitted key means "leave the stored value alone" (that is how a client keeps
 *  a secret it can never read back), and an explicit `null` clears it. The server
 *  merges `extra_config` per key under the same three-valued rule. */
export interface CredentialFields {
  api_key?: string;
  base_url?: string | null;
  extra_config?: Record<string, unknown> | null;
}

export interface ProviderStatus {
  provider: string;
  configured: boolean;
  status: string;
  // Where the credential comes from. `configured` is true for three different
  // sources but only "workspace" is deletable through the credentials API.
  source: "workspace" | "default" | "env" | "none";
  base_url: string | null;
  // Values for DECLARED non-secret fields only. Secret values are never returned.
  extra_config_public: Record<string, string>;
  extra_config_secret_keys: string[];
  /** False when a credential row survives for a provider the catalog no longer
   *  lists. Such a provider has no entry in ModelCatalog.providers, so it has no
   *  display name or credential schema to render — show the slug and offer Remove. */
  catalogued: boolean;
}

export interface ModelConfig {
  tiers: ModelBinding[];
  agent_overrides: ModelBinding[];
  providers: ProviderStatus[];
  warnings: ConfigWarning[];
}

export interface CredentialDeleteResult {
  status: ProviderStatus;
  orphaned_bindings: ConfigWarning[];
}

/** A sender-level mail filter Muldro proposed and the founder confirmed.
 *  A revoked rule is kept rather than deleted (`enabled: false` plus a
 *  `revoked_at` stamp) — it is the record of what was once being filtered. */
export interface FilterRule {
  rule_id: string;
  source: string;
  match_kind: string;
  match_value: string;
  enabled: boolean;
  created_at: string | null;
  revoked_at: string | null;
  created_from_approval_id: string;
}

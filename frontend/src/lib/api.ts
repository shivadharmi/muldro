/** REST client for Jarvis backend API.
 *
 * All calls use the Next.js rewrite: /api/:path* -> backend /v1/:path*
 */

import type {
  AgentRoute,
  AgentPerformance,
  AggregateMetrics,
  Approval,
  ApprovalDetail,
  Artifact,
  Briefing,
  BriefingFeedbackInput,
  BriefingFeedbackSummary,
  CanvasDashboard,
  CommandResponse,
  DLQStats,
  ExecutionItem,
  Goal,
  GoalCreateInput,
  HeartbeatResult,
  MeetingPrep,
  MemoryItem,
  Notification,
  ObservationStatus,
  RunDetail,
  RunStep,
  Schedule,
  ScheduleCreateInput,
  ScheduleUpdateInput,
  SearchResponse,
  StandaloneTask,
  StandaloneTaskCreateInput,
  SystemDashboard,
  Task,
  TaskDetail,
  TraceSummary,
  TraceDetail,
  Workflow,
} from "./types";

import { getStoredToken } from "./auth";

// ── Typed API Error ─────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly statusText: string,
    public readonly body: string
  ) {
    super(`API ${status}: ${body || statusText}`);
    this.name = "ApiError";
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
  get isForbidden(): boolean {
    return this.status === 403;
  }
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

// ── Helpers ─────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getStoredToken() || process.env.NEXT_PUBLIC_API_TOKEN || "";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, text);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function del(path: string): Promise<void> {
  return api<void>(path, { method: "DELETE" });
}

// ── Command ─────────────────────────────────────────────────────

export function sendCommand(message: string): Promise<CommandResponse> {
  return post("/jarvis/command", { command: message });
}

// ── Auth ────────────────────────────────────────────────────

export function sendMagicLink(email: string): Promise<{ status: string; message: string; token?: string }> {
  return post("/auth/magic-link", { email });
}

export function verifyMagicLink(
  token: string
): Promise<{ access_token: string; expires_at: string; user: { user_id: string; email: string; display_name: string } }> {
  return post("/auth/verify", { token });
}

export function getCurrentUser(): Promise<{
  user_id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  status: string;
  onboarding_completed: boolean;
  settings: Record<string, unknown> | null;
}> {
  return api("/auth/me");
}

// ── Settings ────────────────────────────────────────────────

export function fetchSettings(): Promise<{ settings: Record<string, Record<string, unknown>> }> {
  return api("/settings");
}

export function updateSetting(category: string, key: string, value: unknown): Promise<{ status: string }> {
  return api(`/settings/${category}/${key}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

export function fetchPolicyMode(): Promise<{ mode: string }> {
  return api("/settings/policy");
}

export function setPolicyMode(mode: string): Promise<{ mode: string }> {
  return api("/settings/policy/mode", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
}

// ── Connectors ──────────────────────────────────────────────

export function fetchConnectors(): Promise<{ connectors: Array<Record<string, unknown>> }> {
  return api("/connectors");
}

export function createConnector(provider: string): Promise<Record<string, unknown>> {
  return post("/connectors", { provider });
}

export function deleteConnector(id: string): Promise<void> {
  return del(`/connectors/${id}`);
}

export function testConnector(id: string): Promise<Record<string, unknown>> {
  return post(`/connectors/${id}/test`, {});
}

// ── SSE Chat ────────────────────────────────────────────────────

export interface ChatSSEEvent {
  event: string;
  agent?: string;
  model?: string;
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  result?: unknown;
  blocked?: boolean;
  latency_ms?: number;
  message?: string;
  decision?: PlannerOutput;
  trace_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  cost_usd?: number;
  is_thinking?: boolean;
  conversation_id?: string;
}

export async function streamChat(
  message: string,
  onEvent: (event: ChatSSEEvent) => void,
  signal?: AbortSignal,
  conversationId?: string | null
): Promise<void> {
  const body: Record<string, unknown> = { message, surface: "web" };
  if (conversationId) body.conversation_id = conversationId;

  const res = await fetch("/api/jarvis/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, text);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEventType = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        try {
          const parsed = JSON.parse(line.slice(6));
          if (!parsed.event && currentEventType) {
            parsed.event = currentEventType;
          }
          onEvent(parsed);
        } catch {
          // skip malformed JSON
        }
        currentEventType = "";
      }
    }
  }
}

// ── Events ──────────────────────────────────────────────────────

export function fetchRecentEvents(
  hours?: number,
  source?: string,
  limit?: number
): Promise<import("./types").NormalizedEventSummary[]> {
  const params = new URLSearchParams();
  if (hours) params.set("time_range_hours", String(hours));
  if (source) params.set("source", source);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return api(`/events${qs ? `?${qs}` : ""}`);
}

// ── System Dashboard ────────────────────────────────────────────

export function fetchSystemDashboard(): Promise<SystemDashboard> {
  return api("/system/dashboard");
}

export function fetchCanvasDashboard(): Promise<CanvasDashboard> {
  return api("/canvas/dashboard");
}

export function fetchMetrics(): Promise<Record<string, unknown>> {
  return api("/system/metrics");
}

export function fetchDLQStats(): Promise<DLQStats> {
  return api("/system/dlq");
}

export function triggerHeartbeat(): Promise<HeartbeatResult> {
  return post("/system/heartbeat", {});
}

// ── Observations ────────────────────────────────────────────────

export function fetchObservationStatus(): Promise<ObservationStatus[]> {
  return api("/observations/status");
}

// ── Approvals ───────────────────────────────────────────────────

export function fetchApprovals(status?: string): Promise<Approval[]> {
  const qs = status ? `?status=${status}` : "";
  return api(`/approvals${qs}`);
}

export function editApproval(
  id: string,
  body: { title?: string; summary?: string; risk_level?: string }
): Promise<ApprovalDetail> {
  return post(`/approvals/${id}/edit`, body);
}

export function fetchApproval(id: string): Promise<ApprovalDetail> {
  return api(`/approvals/${id}`);
}

export function approveAction(id: string, reason?: string): Promise<ApprovalDetail> {
  return post(`/approvals/${id}/approve`, { reason });
}

export function rejectAction(id: string, reason?: string): Promise<ApprovalDetail> {
  return post(`/approvals/${id}/reject`, { reason });
}

// ── Tasks ───────────────────────────────────────────────────────

export function fetchTasks(): Promise<Task[]> {
  return api("/tasks");
}

export function fetchTask(id: string): Promise<TaskDetail> {
  return api(`/tasks/${id}`);
}

// ── Schedules ───────────────────────────────────────────────────

export function fetchSchedules(): Promise<Schedule[]> {
  return api("/schedules");
}

export function createSchedule(input: ScheduleCreateInput): Promise<Schedule> {
  return post("/schedules", input);
}

export function updateSchedule(id: string, input: ScheduleUpdateInput): Promise<Schedule> {
  return patch(`/schedules/${id}`, input);
}

export function deleteSchedule(id: string): Promise<void> {
  return del(`/schedules/${id}`);
}

export function pauseSchedule(id: string): Promise<Schedule> {
  return post(`/schedules/${id}/pause`, {});
}

export function resumeSchedule(id: string): Promise<Schedule> {
  return post(`/schedules/${id}/resume`, {});
}

// ── Briefings ───────────────────────────────────────────────────

export function fetchBriefing(date: string): Promise<Briefing> {
  return api(`/briefings/${date}`);
}

export function submitBriefingFeedback(
  briefingId: string,
  input: BriefingFeedbackInput
): Promise<{ feedback_id: string; status: string }> {
  return post(`/briefings/${briefingId}/feedback`, input);
}

export function fetchBriefingFeedback(briefingId: string): Promise<BriefingFeedbackSummary> {
  return api(`/briefings/${briefingId}/feedback`);
}

// ── Search ──────────────────────────────────────────────────────

export function searchKnowledge(query: string, scope?: string): Promise<SearchResponse> {
  return post("/search", { query, scope: scope || "all" });
}

// ── Auth ────────────────────────────────────────────────────────

export function getGoogleAuthUrl(): Promise<{ url: string }> {
  return api("/auth/google/authorize");
}

// ── Memories ───────────────────────────────────────────────────

export function fetchMemories(
  memoryType?: string,
  limit?: number
): Promise<{ memories: MemoryItem[] }> {
  const params = new URLSearchParams();
  if (memoryType) params.set("memory_type", memoryType);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return api(`/memories${qs ? `?${qs}` : ""}`);
}

// ── Executions ─────────────────────────────────────────────────

export function fetchExecutions(params?: {
  status?: string;
  source?: string;
  limit?: number;
}): Promise<ExecutionItem[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.source) qs.set("source", params.source);
  if (params?.limit) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return api(`/executions${q ? `?${q}` : ""}`);
}

export function fetchExecution(id: string): Promise<ExecutionItem> {
  return api(`/executions/${id}`);
}

// ── Traces ─────────────────────────────────────────────────────

export function fetchTraces(
  hours?: number,
  trigger?: string,
  agentName?: string,
  limit?: number
): Promise<{ traces: TraceSummary[]; count: number }> {
  const params = new URLSearchParams();
  if (hours) params.set("time_range_hours", String(hours));
  if (trigger) params.set("trigger", trigger);
  if (agentName) params.set("agent_name", agentName);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return api(`/traces${qs ? `?${qs}` : ""}`);
}

export function fetchTraceDetail(traceId: string): Promise<TraceDetail> {
  return api(`/traces/${traceId}`);
}

export function fetchAgentPerformance(hours?: number): Promise<{
  agents: Record<string, AgentPerformance>;
  time_range_hours: number;
}> {
  const qs = hours ? `?time_range_hours=${hours}` : "";
  return api(`/traces/performance${qs}`);
}

export function fetchAggregateMetrics(hours?: number): Promise<AggregateMetrics> {
  const qs = hours ? `?time_range_hours=${hours}` : "";
  return api(`/traces/metrics${qs}`);
}

// ── Triggers ───────────────────────────────────────────────────

export function fetchTriggers(): Promise<{ triggers: Array<Record<string, unknown>> }> {
  return api("/triggers");
}

export function createTrigger(input: Record<string, unknown>): Promise<Record<string, unknown>> {
  return post("/triggers", input);
}

export function deleteTrigger(id: string): Promise<void> {
  return del(`/triggers/${id}`);
}

export function toggleTrigger(
  id: string,
  enabled: boolean
): Promise<Record<string, unknown>> {
  return patch(`/triggers/${id}`, { enabled });
}

// ── UI Surfaces ─────────────────────────────────────────────────

export function fetchSurfaces(userId: string) {
  return api(`/ui/surfaces/${userId}`);
}

export function fetchSurface(userId: string, surfaceId: string) {
  return api(`/ui/surfaces/${userId}/${surfaceId}`);
}

// ── Conversations ───────────────────────────────────────────────

export interface ConversationSummary {
  conversation_id: string;
  title: string | null;
  status: string;
  surface: string;
  last_active_at: string | null;
  message_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  preview: string | null;
  created_at: string | null;
}

export interface MessageToolCall {
  tool_name: string;
  tool_input: Record<string, unknown>;
  result_preview: string | null;
  status: "success" | "error" | "blocked";
  duration_ms: number;
}

export interface MessageAgentStep {
  agent: string;
  model: string | null;
  status: "done" | "error";
  response_text: string | null;
  thinking_preview: string | null;
  reasoning_text: string | null;
  tool_calls: MessageToolCall[];
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
}

export interface PlannerOutput {
  decision: string;
  goal: string;
  reasoning: string;
  priority: "low" | "medium" | "high" | "critical";
  risk_level: "none" | "low" | "medium" | "high";
  execution_mode: "auto_execute" | "approval_required" | "draft_only";
  plan_id: string | null;
  tasks: { task_type: string; input_data: Record<string, unknown> }[];
}

export interface MessageMetadata {
  trace_id: string | null;
  decision: PlannerOutput | null;
  agent_steps: MessageAgentStep[];
}

export interface ConversationMessage {
  message_id: string;
  role: string;
  content: string;
  metadata_: MessageMetadata | null;
  surface: string;
  trace_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  created_at: string | null;
}

export interface ConversationDetailResponse extends ConversationSummary {
  user_id: string;
}

export interface MessageListResponse {
  messages: ConversationMessage[];
  conversation_id: string;
  total: number;
}

export function fetchConversations(status?: string): Promise<ConversationSummary[]> {
  const params = status ? `?status=${status}` : "";
  return api(`/conversations${params}`);
}

export function fetchConversation(id: string): Promise<ConversationDetailResponse> {
  return api(`/conversations/${id}`);
}

export function fetchConversationMessages(
  id: string,
  offset = 0,
  limit = 100
): Promise<MessageListResponse> {
  return api(`/conversations/${id}/messages?offset=${offset}&limit=${limit}`);
}

export function createConversation(
  surface = "web",
  title?: string
): Promise<{ conversation_id: string }> {
  return post("/conversations", { surface, title });
}

export function updateConversation(
  id: string,
  data: { title?: string; status?: string }
): Promise<ConversationSummary> {
  return patch(`/conversations/${id}`, data);
}

export function deleteConversation(id: string): Promise<void> {
  return del(`/conversations/${id}`);
}

// ── Standalone Tasks ────────────────────────────────────────────

export function createTask(input: StandaloneTaskCreateInput): Promise<StandaloneTask> {
  return post("/tasks", input);
}

export function fetchStandaloneTasks(params?: {
  status?: string;
  goal_id?: string;
  task_type?: string;
  priority?: string;
}): Promise<StandaloneTask[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.goal_id) qs.set("goal_id", params.goal_id);
  if (params?.task_type) qs.set("task_type", params.task_type);
  if (params?.priority) qs.set("priority", params.priority);
  const q = qs.toString();
  return api(`/tasks${q ? `?${q}` : ""}`);
}

export function startTask(id: string): Promise<StandaloneTask> {
  return post(`/tasks/${id}/start`, {});
}

export function cancelTask(id: string): Promise<StandaloneTask> {
  return post(`/tasks/${id}/cancel`, {});
}

export function resumeTask(id: string): Promise<StandaloneTask> {
  return post(`/tasks/${id}/resume`, {});
}

export function addTaskDependency(
  taskId: string,
  dependsOnTaskId: string,
  dependencyType?: string
): Promise<Record<string, unknown>> {
  return post(`/tasks/${taskId}/dependencies`, {
    depends_on_task_id: dependsOnTaskId,
    dependency_type: dependencyType || "blocks",
  });
}

// ── Goals ───────────────────────────────────────────────────────

export async function fetchGoals(status?: string): Promise<Goal[]> {
  const qs = status ? `?status=${status}` : "";
  const data = await api<{ goals: Goal[] }>(`/goals${qs}`);
  return data.goals ?? [];
}

export function createGoal(input: GoalCreateInput): Promise<Goal> {
  return post("/goals", input);
}

export function updateGoal(
  id: string,
  input: Partial<GoalCreateInput & { status: string; progress: number }>
): Promise<Goal> {
  return patch(`/goals/${id}`, input);
}

export function deleteGoal(id: string): Promise<void> {
  return del(`/goals/${id}`);
}

// ── Notifications ───────────────────────────────────────────────

export function fetchNotifications(
  status?: string,
  limit?: number
): Promise<Notification[]> {
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (limit) qs.set("limit", String(limit));
  const q = qs.toString();
  return api(`/notifications${q ? `?${q}` : ""}`);
}

export function markNotificationRead(id: string): Promise<void> {
  return post(`/notifications/${id}/read`, {});
}

export function dismissNotification(id: string): Promise<void> {
  return post(`/notifications/${id}/dismiss`, {});
}

// ── Workflows ───────────────────────────────────────────────────

export function fetchWorkflows(): Promise<Workflow[]> {
  return api("/workflows");
}

export function startWorkflow(
  name: string,
  params?: Record<string, unknown>
): Promise<Record<string, unknown>> {
  return post(`/workflows/${name}/start`, params || {});
}

// ── Artifacts ───────────────────────────────────────────────────

export async function fetchArtifacts(limit?: number): Promise<Artifact[]> {
  const qs = limit ? `?limit=${limit}` : "";
  const data = await api<{ artifacts: Artifact[] }>(`/artifacts${qs}`);
  return data.artifacts ?? [];
}

export function fetchArtifact(id: string): Promise<Artifact> {
  return api(`/artifacts/${id}`);
}

// ── Realtime SSE (fetch-based, sends Authorization header) ──────

export function subscribeToEvents(
  onEvent: (event: { event_type: string; data: Record<string, unknown> }) => void,
  signal?: AbortSignal
): void {
  const headers = authHeaders();

  fetch("/api/realtime/events", {
    headers: { Accept: "text/event-stream", ...headers },
    signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch {
              // skip malformed
            }
          }
        }
      }
    })
    .catch(() => {
      // connection closed or aborted
    });
}

// ── Runs ────────────────────────────────────────────────────────

export function fetchRun(runId: string): Promise<RunDetail> {
  return api(`/runs/${runId}`);
}

export function fetchRunSteps(runId: string): Promise<RunStep[]> {
  return api(`/runs/${runId}/steps`);
}

export function fetchRunTrace(runId: string): Promise<TraceDetail> {
  return api(`/runs/${runId}/trace`);
}

export function resumeRun(runId: string): Promise<RunDetail> {
  return post(`/runs/${runId}/resume`, {});
}

export function fetchRunArtifacts(runId: string): Promise<Artifact[]> {
  return api(`/runs/${runId}/artifacts`);
}

// ── Routes ──────────────────────────────────────────────────────

export function fetchRoutes(): Promise<AgentRoute[]> {
  return api("/routes");
}

export function createRoute(input: Partial<AgentRoute>): Promise<AgentRoute> {
  return post("/routes", input);
}

export function updateRoute(id: string, data: Partial<AgentRoute>): Promise<AgentRoute> {
  return patch(`/routes/${id}`, data);
}

export function deleteRoute(id: string): Promise<void> {
  return del(`/routes/${id}`);
}

// ── Budget ──────────────────────────────────────────────────────

export function fetchBudget(): Promise<{ daily_limit_usd: number }> {
  return api("/settings/budget");
}

export function updateBudgetLimit(usd: number): Promise<{ daily_limit_usd: number }> {
  return api("/settings/budget/daily_limit", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ daily_limit_usd: usd }),
  });
}

// ── Meeting Prep ────────────────────────────────────────────────────

export function generateMeetingPrep(
  meetingId?: string,
  next?: boolean
): Promise<MeetingPrep> {
  return post("/meetings/prep", { meeting_id: meetingId, next });
}

// ── Agents ────────────────────────────────────────────────────────

export interface AgentRecord {
  agent_id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  system_prompt: string | null;
  model_tier: string;
  tool_scope: string[] | null;
  max_tokens: number;
  temperature: number;
  enabled: boolean;
  calls_today: number;
  success_rate: number;
  avg_latency_ms: number;
  last_invoked_at: string | null;
}

export function fetchAgents(): Promise<AgentRecord[]> {
  return api("/agents");
}

export function updateAgent(
  agentId: string,
  data: Partial<AgentRecord>
): Promise<AgentRecord> {
  return patch(`/agents/${agentId}`, data);
}

export function toggleAgent(
  agentId: string,
  enabled: boolean
): Promise<AgentRecord> {
  return post(`/agents/${agentId}/${enabled ? "enable" : "disable"}`, {});
}

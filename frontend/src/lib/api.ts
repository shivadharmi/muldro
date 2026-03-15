/** REST client for Jarvis backend API.
 *
 * All calls use the Next.js rewrite: /api/:path* -> backend /v1/:path*
 */

import type {
  Approval,
  ApprovalDetail,
  Briefing,
  BriefingFeedbackInput,
  BriefingFeedbackSummary,
  CanvasDashboard,
  CommandResponse,
  DLQStats,
  HeartbeatResult,
  ObservationStatus,
  Schedule,
  ScheduleCreateInput,
  ScheduleUpdateInput,
  SearchResponse,
  SystemDashboard,
  Task,
  TaskDetail,
} from "./types";

import { getStoredToken } from "./auth";

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
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
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

export function sendMagicLink(email: string): Promise<{ status: string; message: string }> {
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
  decision?: Record<string, unknown>;
  trace_id?: string;
  input_tokens?: number;
  output_tokens?: number;
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
    throw new Error(`Chat API ${res.status}: ${text || res.statusText}`);
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

export function fetchApprovals(): Promise<Approval[]> {
  return api("/approvals");
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
): Promise<{ memories: Array<Record<string, unknown>> }> {
  const params = new URLSearchParams();
  if (memoryType) params.set("memory_type", memoryType);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return api(`/memories${qs ? `?${qs}` : ""}`);
}

// ── Executions ─────────────────────────────────────────────────

export function fetchExecutions(): Promise<Array<Record<string, unknown>>> {
  return api("/executions");
}

export function fetchExecution(id: string): Promise<Record<string, unknown>> {
  return api(`/executions/${id}`);
}

// ── Traces ─────────────────────────────────────────────────────

export function fetchTraces(
  hours?: number,
  trigger?: string,
  limit?: number
): Promise<{ traces: Array<Record<string, unknown>> }> {
  const params = new URLSearchParams();
  if (hours) params.set("hours", String(hours));
  if (trigger) params.set("trigger", trigger);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return api(`/traces${qs ? `?${qs}` : ""}`);
}

export function fetchAgentPerformance(): Promise<Record<string, Record<string, unknown>>> {
  return api("/traces/performance");
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
  status: string;
  surface: string;
  last_active_at: string | null;
  message_count: number;
  preview: string | null;
  created_at: string | null;
}

export interface ConversationMessage {
  message_id: string;
  role: string;
  content: string;
  metadata_: Record<string, unknown> | null;
  surface: string;
  created_at: string | null;
}

export function fetchConversations(): Promise<ConversationSummary[]> {
  return api("/conversations");
}

export function fetchConversationMessages(
  id: string
): Promise<{ messages: ConversationMessage[]; conversation_id: string }> {
  return api(`/conversations/${id}/messages`);
}

export function createConversation(): Promise<{ conversation_id: string }> {
  return post("/conversations", { surface: "web" });
}

export function deleteConversation(id: string): Promise<void> {
  return del(`/conversations/${id}`);
}

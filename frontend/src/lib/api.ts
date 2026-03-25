/** REST client for Jarvis backend API.
 *
 * All calls use the Next.js rewrite: /api/:path* -> backend /v1/:path*
 */

import type {
  Approval,
  ApprovalDetail,
  Artifact,
  Briefing,
  BriefingFeedbackInput,
  BriefingFeedbackSummary,
  CanvasDashboard,
  CommandResponse,
  MeetingPrep,
  MemoryItem,
  Notification,
  SearchResponse,
  SystemDashboard,
} from "./types";

import type { A2UISurface } from "./a2ui-types";
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
  message_id?: string;
  decision?: PlannerOutput;
  trace_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  cost_usd?: number;
  is_thinking?: boolean;
  conversation_id?: string;
  // A2UI surface fields (event: "surface")
  type?: string;
  id?: string;
  children?: unknown[];
  metadata?: Record<string, unknown>;
}

export async function streamChat(
  message: string,
  onEvent: (event: ChatSSEEvent) => void,
  signal?: AbortSignal,
  conversationId?: string | null,
  mode?: string,
): Promise<void> {
  const body: Record<string, unknown> = { message, surface: "web" };
  if (conversationId) body.conversation_id = conversationId;
  if (mode) body.mode = mode;

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

// ── Observations ────────────────────────────────────────────────

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

// ── Schedules ───────────────────────────────────────────────────

// ── Briefings ───────────────────────────────────────────────────

export interface HomeFeedData {
  since_last_visit: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  priority_items: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  live_activity: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  recommended_actions: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  recent_intelligence: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  capability_health: any[];
}

export function fetchHomeFeed(): Promise<HomeFeedData> {
  return api("/home");
}

export interface BriefingListItem {
  briefing_id: string;
  headline: string | null;
  date: string | null;
  status: string | null;
  domain: string | null;
  confidence: number | null;
  created_at: string | null;
}

export interface BriefingDetailData {
  briefing_id: string;
  headline: string | null;
  full_text: string | null;
  date: string | null;
  confidence: number | null;
  evidence: import("./types/context").EvidenceBundle | undefined;
  related_items: Array<{ item_type: string; item_id: string; title: string; status: string }>;
  actions: Array<{ action: string; label: string }>;
}

export function fetchBriefingList(limit = 50): Promise<BriefingListItem[]> {
  return api(`/briefings?limit=${limit}`);
}

export function fetchBriefingDetail(briefingId: string): Promise<BriefingDetailData | null> {
  return api(`/briefings/detail/${briefingId}`);
}

export function briefingAction(
  briefingId: string,
  action: string
): Promise<{ status: string }> {
  return post(`/briefings/${briefingId}/${action}`, {});
}

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

export interface UnifiedSearchResponse {
  total_count: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  groups: Record<string, any[]>;
}

export function searchUnified(query: string, limit = 20): Promise<UnifiedSearchResponse> {
  return post("/search/unified", { query, limit });
}

// ── Auth ────────────────────────────────────────────────────────

export interface AuthProvider {
  name: string;
  display_name: string;
  type: string;
  configured: boolean;
  connected: boolean;
  scopes: string[];
}

export function fetchAuthProviders(): Promise<{ providers: AuthProvider[] }> {
  return api("/auth/providers");
}

export function getAuthUrl(
  provider: string,
): Promise<{ url: string; provider: string }> {
  return api(`/auth/${provider}/authorize`);
}

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


// ── UI Surfaces ─────────────────────────────────────────────────

export function fetchSurfaces() {
  return api("/ui/surfaces");
}

export function fetchSurface(surfaceId: string) {
  return api(`/ui/surfaces/${surfaceId}`);
}

export function fetchWorkspaceSurfaces(): Promise<{ surfaces: A2UISurface[]; count: number }> {
  return api("/workspace/surfaces");
}

// ── Message Context / Evidence ──────────────────────────────────

export function fetchMessageContext(messageId: string) {
  return api<import("@/lib/types/context").ContextSidebarData>(
    `/conversations/messages/${messageId}/context`
  );
}

export function fetchMessageEvidence(messageId: string) {
  return api<import("@/lib/types/context").EvidenceBundle>(
    `/conversations/messages/${messageId}/evidence`
  );
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

// ── Integrations / MCP ──────────────────────────────────────────

export interface Installation {
  install_id: string;
  server_name: string;
  display_name: string;
  transport: string;
  status: string;
  health_status: string;
  trust_id: string | null;
  auth_provider: string | null;
  enabled: boolean;
  created_at: string | null;
}

export function fetchInstallations(): Promise<Installation[]> {
  return api("/integrations");
}

export function deleteInstallation(installId: string): Promise<void> {
  return del(`/integrations/${installId}`);
}

export function checkInstallationHealth(
  installId: string
): Promise<{ install_id?: string; server_name?: string; health_status: string }> {
  return api(`/integrations/${installId}/health`);
}

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
  MeetingPrep,
  MemoryItem,
  Notification,
  PlanOutput,
  SearchResponse,
  SystemDashboard,
  TrustDashboardEntry,
  TrustCapabilityDetail,
  TimePolicyRule,
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
  plan?: PlanOutput;
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

export function searchAll(
  query: string,
  types?: string[],
  limit = 20
): Promise<SearchResponse> {
  return post("/search", { query, types: types || null, limit });
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


// ── Insights ───────────────────────────────────────────────────

export function dismissInsight(
  surfaceId: string,
  reason?: string
): Promise<{ status: string; surface_id: string }> {
  return post(`/insights/${surfaceId}/dismiss`, { reason: reason || null });
}

// ── UI Surfaces ─────────────────────────────────────────────────

export function fetchSurfaces() {
  return api("/ui/surfaces");
}

export function fetchSurface(surfaceId: string) {
  return api(`/ui/surfaces/${surfaceId}`);
}

interface WorkspaceSurfaceResponse {
  id: string;
  kind: import("@/lib/types/surfaces").SurfaceKind;
  preview: import("@/lib/a2ui-types").SurfacePreview;
  detail_config: import("@/lib/a2ui-types").DetailConfig | null;
  source_run_id?: string | null;
  response_preview?: string | null;
  created_at?: string | null;
  phase?: import("@/lib/a2ui-types").ExecutionPhase;
  steps?: import("@/lib/a2ui-types").StepState[];
  current_step?: string | null;
  progress?: string;
  approval?: import("@/lib/a2ui-types").ApprovalContext | null;
  results?: import("@/lib/a2ui-types").ResultSummary | null;
  surface_data?: import("@/lib/a2ui-types").SurfaceDataPayload | null;
}

export function fetchWorkspaceSurfaces(): Promise<{ surfaces: WorkspaceSurfaceResponse[]; count: number }> {
  return api("/workspace/surfaces");
}

export function fetchSurfaceDetail(
  surfaceId: string,
  tabId: string
): Promise<import("@/lib/a2ui-types").DetailTabResponse> {
  return api(`/surfaces/${surfaceId}/detail/${tabId}`);
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

export type { PlanStep, CapabilityGap, PlanOutput } from "./types";

export interface MessageMetadata {
  trace_id: string | null;
  plan: PlanOutput | null;
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

export interface UnifiedIntegration {
  server_name: string;
  display_name: string;
  category: "oauth" | "token" | "local";
  provider: string | null;
  configured: boolean;
  connected: boolean;
  health_status: string;
  scopes: string[];
  install_id: string | null;
}

export function fetchInstallations(): Promise<Installation[]> {
  return api("/integrations");
}

export function fetchUnifiedIntegrations(): Promise<UnifiedIntegration[]> {
  return api("/integrations/unified");
}

export function deleteInstallation(installId: string): Promise<void> {
  return del(`/integrations/${installId}`);
}

export function checkInstallationHealth(
  installId: string
): Promise<{ install_id?: string; server_name?: string; health_status: string }> {
  return api(`/integrations/${installId}/health`);
}

// ── Knowledge Page ──────────────────────────────────────────────

export interface KnowledgeGraphNode {
  entity_id: string;
  canonical_name: string;
  entity_type: string;
  importance_score: number;
  interaction_count: number;
  last_seen_at: string | null;
  attributes: Record<string, unknown> | null;
  aliases: string[];
}

export interface KnowledgeGraphEdge {
  from_entity_id?: string;
  to_entity_id?: string;
  from?: string;
  to?: string;
  relation_type?: string;
  type?: string;
  relation_id?: string;
}

export interface KnowledgeGraphResponse {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  stats: { total_entities: number; total_relationships: number };
}

export interface KnowledgeMemoryItem {
  memory_id: string;
  memory_type: string;
  fact_text: string;
  confidence: number;
  stability_score: number;
  refresh_count: number;
  scope: string | null;
  created_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
  entity_ids: string[];
  entity_names: string[];
}

export interface KnowledgeMemoryListResponse {
  items: KnowledgeMemoryItem[];
  total: number;
  page: number;
  pages: number;
}

export interface KnowledgeMemoryDetail {
  memory_id: string;
  memory_type: string;
  fact_text: string;
  confidence: number;
  stability_score: number;
  refresh_count: number;
  scope: string | null;
  created_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
  linked_entities: { entity_id: string; canonical_name: string; entity_type: string }[];
  provenance: { source_event_ids: string[]; source_description: string | null };
}

export interface KnowledgeStatsResponse {
  total_entities: number;
  total_relationships: number;
  total_memories: number;
  avg_confidence: number;
  weekly_delta: { entities: number; relationships: number; memories: number };
  entity_counts_by_type: { entity_type: string; count: number }[];
  memory_counts_by_type: { memory_type: string; count: number }[];
  central_entities: { entity_id: string; name: string; entity_type: string; degree: number }[];
  communities: { seed_entity_id: string; seed_name: string; seed_type: string; community_size: number; community_members: string[] }[];
  stale_relationships: { relation_id: string; from_name: string; to_name: string; relation_type: string }[];
  growth_by_day: { date: string; entities: number; memories: number }[];
}

export function fetchKnowledgeGraph(): Promise<KnowledgeGraphResponse> {
  return api("/knowledge/graph");
}

export function fetchKnowledgeMemories(params?: {
  type?: string;
  sort_by?: string;
  search?: string;
  entity_id?: string;
  page?: number;
  limit?: number;
}): Promise<KnowledgeMemoryListResponse> {
  const qs = new URLSearchParams();
  if (params?.type) qs.set("type", params.type);
  if (params?.sort_by) qs.set("sort_by", params.sort_by);
  if (params?.search) qs.set("search", params.search);
  if (params?.entity_id) qs.set("entity_id", params.entity_id);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.limit) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return api(`/knowledge/memories${q ? `?${q}` : ""}`);
}

export function fetchKnowledgeMemoryDetail(
  memoryId: string,
): Promise<KnowledgeMemoryDetail> {
  return api(`/knowledge/memories/${memoryId}`);
}

export function fetchKnowledgeStats(): Promise<KnowledgeStatsResponse> {
  return api("/knowledge/stats");
}

// ── Trust ──────────────────────────────────────────────────────

export async function fetchTrustDashboard(): Promise<{
  capabilities: TrustDashboardEntry[];
}> {
  return api("/trust/dashboard");
}

export async function fetchTrustCapability(
  capability: string
): Promise<TrustCapabilityDetail> {
  return api(`/trust/${capability}`);
}

export async function setTrustCeiling(
  capability: string,
  maxLevel: string
): Promise<{ capability: string; max_level: string }> {
  return api(`/trust/${capability}/ceiling`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_level: maxLevel }),
  });
}

export async function resetTrust(
  capability: string
): Promise<{ capability: string; status: string }> {
  return api(`/trust/${capability}/reset`, { method: "POST" });
}

export async function fetchTimePolicies(): Promise<{
  policies: TimePolicyRule[];
}> {
  return api("/trust-time-policies");
}

export async function setTimePolicies(
  policies: TimePolicyRule[]
): Promise<{ policies: TimePolicyRule[] }> {
  return api("/trust-time-policies", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policies }),
  });
}

// ── History ─────────────────────────────────────────────────────

export async function fetchHistory(params: {
  status?: string;
  source?: string;
  search?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<{
  items: unknown[];
  total: number;
  limit: number;
  offset: number;
}> {
  const qs = new URLSearchParams();
  if (params.status && params.status !== "all") qs.set("status", params.status);
  if (params.source && params.source !== "all") qs.set("source", params.source);
  if (params.search) qs.set("search", params.search);
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return api(`/history${query ? `?${query}` : ""}`);
}

export async function fetchHistoryDetail(runId: string) {
  return api<Record<string, unknown>>(`/history/${runId}`);
}

export async function retryRun(runId: string) {
  return post<{ run_id: string; status: string; message: string }>(
    `/history/${runId}/retry`,
    {}
  );
}

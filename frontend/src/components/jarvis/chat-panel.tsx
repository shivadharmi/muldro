"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, streamChat, type ChatSSEEvent, type ConversationMessage, type PlanOutput } from "@/lib/api";
import { formatApiError, parseSseError } from "@/lib/api-error";
import { useCommandStore } from "@/stores/command-store";
import { useShellStore } from "@/stores/shell-store";
import { CommandInput } from "./command-input";
import { MarkdownRenderer } from "./markdown-renderer";
import { AgentTrace, type AgentStep } from "./agent-trace";
import { StepList } from "@/components/a2ui/components/step-list";
import type { StepState } from "@/lib/a2ui-types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  traceId?: string;
  plan?: PlanOutput;
  agents: AgentStep[];
  streaming?: boolean;
}

interface ChatPanelProps {
  conversationId?: string | null;
  initialMessages?: ConversationMessage[];
  onConversationCreated?: (id: string) => void;
  onMessageSent?: () => void;
  onSurface?: (surface: { id: string; children: unknown[]; metadata: Record<string, unknown> }) => void;
}

function backendMessagesToChat(messages: ConversationMessage[]): ChatMessage[] {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => {
      // Restore agent steps from persisted metadata
      const agents: AgentStep[] = (m.metadata_?.agent_steps ?? []).map((step) => ({
        agent: step.agent,
        model: step.model ?? undefined,
        status: step.status === "error" ? ("error" as const) : ("done" as const),
        thinking: step.reasoning_text ? [step.reasoning_text] : [],
        realThinking: step.thinking_preview ? [step.thinking_preview] : [],
        streamingText: "",
        toolCalls: (step.tool_calls ?? []).map((tc) => ({
          tool: tc.tool_name,
          input: tc.tool_input ?? {},
          result: tc.result_preview ? tryParseJson(tc.result_preview) : undefined,
          blocked: tc.status === "blocked",
          latencyMs: tc.duration_ms || undefined,
        })),
        text: step.response_text ?? undefined,
        inputTokens: step.input_tokens ?? undefined,
        outputTokens: step.output_tokens ?? undefined,
        cacheCreationTokens: step.cache_creation_tokens ?? undefined,
        cacheReadTokens: step.cache_read_tokens ?? undefined,
        costUsd: step.cost_usd ?? undefined,
        latencyMs: step.latency_ms ?? undefined,
      }));

      return {
        id: m.message_id,
        role: m.role as "user" | "assistant",
        content: m.content,
        timestamp: m.created_at || new Date().toISOString(),
        traceId: m.metadata_?.trace_id ?? undefined,
        plan: m.metadata_?.plan ?? undefined,
        agents,
      };
    });
}

function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

interface MessageMetrics {
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  latencyMs: number;
  hasData: boolean;
}

/** Roll the per-agent token/cost/latency already in state up to one
 *  message-level total for the design's footer line. */
function aggregateMetrics(agents: AgentStep[]): MessageMetrics {
  const totals = agents.reduce(
    (acc, a) => ({
      inputTokens: acc.inputTokens + (a.inputTokens ?? 0),
      outputTokens: acc.outputTokens + (a.outputTokens ?? 0),
      costUsd: acc.costUsd + (a.costUsd ?? 0),
      latencyMs: acc.latencyMs + (a.latencyMs ?? 0),
    }),
    { inputTokens: 0, outputTokens: 0, costUsd: 0, latencyMs: 0 },
  );
  return {
    ...totals,
    hasData: totals.inputTokens > 0 || totals.outputTokens > 0 || totals.costUsd > 0,
  };
}

function formatCost(costUsd: number): string {
  return costUsd < 0.01 ? costUsd.toFixed(4) : costUsd.toFixed(3);
}

function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** Build a StepState[] from the message's PlanOutput so a chat-only user can
 *  see the plan → pipeline steps inline (reusing the faithful StepList).
 *
 *  The chat SSE stream carries the plan and per-agent progress, but not a
 *  per-step live status feed (that flows through the autonomous WS path into the
 *  surfaces pane). We derive a faithful-but-honest status: while streaming, the
 *  first not-yet-done step is "executing" and the rest "pending"; once the turn
 *  finishes, every step is "completed". */
function planToStepStates(msg: ChatMessage): StepState[] {
  const steps = msg.plan?.steps ?? [];
  if (steps.length === 0) return [];

  const doneAgents = msg.agents.filter((a) => a.status === "done").length;
  const failed = msg.agents.some((a) => a.status === "error");

  return steps.map((s, i): StepState => {
    let status: StepState["status"];
    if (!msg.streaming) {
      status = failed && i === steps.length - 1 ? "failed" : "completed";
    } else if (i < doneAgents) {
      status = "completed";
    } else if (i === doneAgents) {
      status = "executing";
    } else {
      status = "pending";
    }
    return {
      step_id: s.step_id,
      description: s.description,
      status,
      output_summary: s.user_context,
      duration_ms: null,
      started_at: null,
      completed_at: null,
      timeout_seconds: null,
      error: null,
      retry_count: null,
    };
  });
}

export function ChatPanel({
  conversationId,
  initialMessages,
  onConversationCreated,
  onMessageSent,
  onSurface,
}: ChatPanelProps) {
  // Restore messages from cache on mount, fall back to initialMessages
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const { cachedMessages, conversationId: cachedConvoId } = useCommandStore.getState();
    // Restore if we have cached messages for the same (or no) conversation
    if (cachedMessages.length > 0 && (!conversationId || cachedConvoId === conversationId)) {
      return cachedMessages.map((snap) => ({
        id: snap.id,
        role: snap.role,
        content: snap.content,
        timestamp: snap.timestamp,
        agents: [],
        streaming: false,
      }));
    }
    if (initialMessages) {
      return backendMessagesToChat(initialMessages);
    }
    return [];
  });
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeConvoRef = useRef<string | null>(conversationId ?? null);

  // Sync conversationId to ref + store
  useEffect(() => {
    activeConvoRef.current = conversationId ?? null;
    useCommandStore.getState().setConversationId(conversationId ?? null);
  }, [conversationId]);

  // Reset messages when conversation changes:
  // - sidebar selection: initialMessages populated → render them
  // - new chat: conversationId becomes null → clear messages
  useEffect(() => {
    if (initialMessages && initialMessages.length > 0) {
      setMessages(backendMessagesToChat(initialMessages));
    }
  }, [initialMessages]);

  // Clear messages when switching to a new (blank) conversation
  const prevConvoRef = useRef<string | null | undefined>(conversationId);
  useEffect(() => {
    if (prevConvoRef.current !== undefined && prevConvoRef.current !== null && conversationId === null) {
      // Had a conversation before, now null → user clicked "New Chat"
      setMessages([]);
    }
    prevConvoRef.current = conversationId;
  }, [conversationId]);

  // Save message snapshots to store for cross-route restoration
  useEffect(() => {
    if (messages.length === 0) return;
    const snapshots = messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
    }));
    useCommandStore.getState().setCachedMessages(snapshots);
  }, [messages]);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    });
  }, []);

  const handleSubmit = async (message: string) => {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: message,
      timestamp: new Date().toISOString(),
      agents: [],
    };

    let assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
      agents: [],
      streaming: true,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setLoading(true);
    scrollToBottom();

    const abort = new AbortController();
    abortRef.current = abort;

    const updateAssistant = (updater: (msg: ChatMessage) => ChatMessage) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? updater(m) : m))
      );
    };

    try {
      await streamChat(
        message,
        (event: ChatSSEEvent) => {
          switch (event.event) {
            case "conversation":
              if (event.conversation_id && onConversationCreated) {
                activeConvoRef.current = event.conversation_id;
                onConversationCreated(event.conversation_id);
              }
              break;

            case "trace":
              updateAssistant((m) => ({
                ...m,
                traceId: event.trace_id,
              }));
              break;

            case "message_id": {
              const newId = event.message_id || assistantId;
              updateAssistant((m) => ({ ...m, id: newId }));
              assistantId = newId; // keep closure in sync
              break;
            }

            case "agent_start":
              updateAssistant((m) => ({
                ...m,
                agents: [
                  ...m.agents,
                  {
                    agent: event.agent || "unknown",
                    model: event.model,
                    status: "running",
                    thinking: [],
                    realThinking: [],
                    streamingText: "",
                    toolCalls: [],
                  },
                ],
              }));
              scrollToBottom();
              break;

            case "thinking":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? event.is_thinking
                      ? { ...a, realThinking: [...a.realThinking, event.text || ""] }
                      : { ...a, thinking: [...a.thinking, event.text || ""] }
                    : a
                ),
              }));
              scrollToBottom();
              break;

            case "text_delta":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? { ...a, streamingText: a.streamingText + (event.text || "") }
                    : a
                ),
              }));
              scrollToBottom();
              break;

            case "plan":
              updateAssistant((m) => ({
                ...m,
                plan: event.plan,
              }));
              break;

            case "agent_done":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? {
                        ...a,
                        status: "done" as const,
                        text: event.text,
                        inputTokens: event.input_tokens,
                        outputTokens: event.output_tokens,
                        cacheCreationTokens: event.cache_creation_tokens,
                        cacheReadTokens: event.cache_read_tokens,
                        costUsd: event.cost_usd,
                        latencyMs: event.latency_ms,
                      }
                    : a
                ),
              }));
              scrollToBottom();
              break;

            case "response":
              updateAssistant((m) => ({
                ...m,
                content: event.text || "",
              }));
              scrollToBottom();
              break;

            case "error": {
              const parsed = parseSseError(event);
              updateAssistant((m) => ({
                ...m,
                content: m.content || `Error: ${formatApiError(parsed)}`,
                streaming: false,
              }));
              break;
            }

            case "surface":
              if (onSurface && event.id && event.metadata) {
                onSurface({
                  id: event.id,
                  children: event.children ?? [],
                  metadata: event.metadata,
                });
              }
              break;

            case "tool_call":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? {
                        ...a,
                        toolCalls: [
                          ...a.toolCalls,
                          {
                            tool: event.tool || "unknown",
                            input: (event.input ?? {}) as Record<string, unknown>,
                          },
                        ],
                      }
                    : a
                ),
              }));
              break;

            case "tool_result":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? {
                        ...a,
                        toolCalls: a.toolCalls.map((tc, i) =>
                          i === a.toolCalls.length - 1
                            ? { ...tc, result: event.result }
                            : tc
                        ),
                      }
                    : a
                ),
              }));
              break;

            case "done":
              updateAssistant((m) => ({
                ...m,
                streaming: false,
              }));
              scrollToBottom();
              break;
          }
        },
        abort.signal,
        activeConvoRef.current,
        useCommandStore.getState().mode,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const safe =
          err instanceof ApiError
            ? err.displayMessage
            : "Something went wrong.";
        updateAssistant((m) => ({
          ...m,
          content: `Error: ${safe}`,
          streaming: false,
        }));
      }
    } finally {
      updateAssistant((m) => ({ ...m, streaming: false }));
      setLoading(false);
      abortRef.current = null;
      onMessageSent?.();
    }
  };


  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-4 p-4">
        {messages.length === 0 && (
          <div className="text-t-tertiary text-sm text-center mt-8 space-y-2">
            <p className="text-lg text-t-secondary">Talk to Jarvis</p>
            <p>
              Ask anything — Jarvis routes through its multi-agent system to
              observe, plan, decide, and respond.
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.role === "user" ? (
              <UserBubble content={msg.content} />
            ) : (
              <AssistantMessage msg={msg} />
            )}
          </div>
        ))}
      </div>
      <div className="border-t border-b-primary p-4">
        <CommandInput onSubmit={handleSubmit} disabled={loading} />
      </div>
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-[var(--radius-lg)] px-3.5 py-2.5 text-sm bg-surface-3 text-t-primary border border-b-secondary">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}

function AssistantMessage({ msg }: { msg: ChatMessage }) {
  const focusedId = useCommandStore((s) => s.focusedMessageId);
  const setFocused = useCommandStore((s) => s.setFocusedMessageId);
  const isFocused = focusedId === msg.id;
  const metrics = aggregateMetrics(msg.agents);
  const planSteps = planToStepStates(msg);
  const currentStep = planSteps.find((s) => s.status === "executing")?.step_id ?? null;

  const handleFocus = () => {
    if (msg.streaming) return;
    const next = isFocused ? null : msg.id;
    setFocused(next);
    if (next && !useShellStore.getState().rightSidebarOpen) {
      useShellStore.getState().toggleRightSidebar();
    }
  };

  return (
    <div
      className={`flex justify-start cursor-pointer transition-all ${isFocused ? "ring-1 ring-accent-primary/40 rounded-[var(--radius-lg)]" : ""}`}
      onClick={handleFocus}
    >
      <div className="max-w-[95%] w-full space-y-2">
        {/* Agent pipeline ("how Jarvis answered") */}
        <AgentTrace agents={msg.agents} plan={msg.plan ?? null} streaming={!!msg.streaming} />

        {/* Inline plan → pipeline steps (reuses the faithful StepList so a
            chat-only user sees the pipeline, not just the surfaces pane). */}
        {planSteps.length > 0 && (
          <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-primary px-3 py-2.5">
            <p className="text-[10px] uppercase tracking-wider text-t-tertiary mb-2">Pipeline</p>
            <StepList steps={planSteps} currentStep={currentStep} />
          </div>
        )}

        {/* Final response */}
        {msg.content ? (
          <div className="rounded-[var(--radius-lg)] px-3.5 py-2.5 text-sm bg-surface-2 text-t-primary border-l-2 border-l-j-primary">
            <MarkdownRenderer content={msg.content} />
          </div>
        ) : msg.streaming ? (
          <div className="rounded-[var(--radius-lg)] px-3.5 py-2.5 text-sm bg-surface-2 text-t-tertiary border-l-2 border-l-j-primary">
            {/* Show presenter streaming text as live response */}
            {(() => {
              const presenterAgent = msg.agents.find(
                (a) => a.agent === "presenter" && a.status === "running" && a.streamingText
              );
              if (presenterAgent?.streamingText) {
                return <MarkdownRenderer content={presenterAgent.streamingText} />;
              }
              return (
                <span className="inline-flex items-center gap-1">
                  <span>{msg.agents.length > 0 ? "Thinking" : "Connecting to Jarvis"}</span>
                  <span className="w-1.5 h-4 bg-j-primary animate-caret rounded-sm" />
                </span>
              );
            })()}
          </div>
        ) : null}

        {/* Message footer: trace id + rolled-up tokens / cost / latency */}
        {!msg.streaming && (msg.traceId || metrics.hasData) && (
          <div className="flex items-center gap-2 px-2 text-[10px] text-t-muted">
            {msg.traceId && <span>trace: {msg.traceId}</span>}
            {metrics.hasData && (
              <span className="font-mono tabular-nums">
                {metrics.inputTokens.toLocaleString()} in /{" "}
                {metrics.outputTokens.toLocaleString()} out tok ·{" "}
                <span className="text-j-success">${formatCost(metrics.costUsd)}</span>
                {metrics.latencyMs > 0 && ` · ${formatLatency(metrics.latencyMs)}`}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

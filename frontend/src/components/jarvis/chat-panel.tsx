"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat, type ChatSSEEvent, type ConversationMessage, type PlanOutput } from "@/lib/api";
import { useCommandStore } from "@/stores/command-store";
import { useShellStore } from "@/stores/shell-store";
import { CommandInput } from "./command-input";
import { MarkdownRenderer } from "./markdown-renderer";

interface AgentStep {
  agent: string;
  model?: string;
  status: "running" | "done" | "error";
  thinking: string[];
  realThinking: string[];
  streamingText: string;
  toolCalls: {
    tool: string;
    input: Record<string, unknown>;
    result?: unknown;
    blocked?: boolean;
    latencyMs?: number;
  }[];
  text?: string;
  inputTokens?: number;
  outputTokens?: number;
  cacheCreationTokens?: number;
  cacheReadTokens?: number;
  costUsd?: number;
  latencyMs?: number;
}

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

            case "error":
              updateAssistant((m) => ({
                ...m,
                content: m.content || `Error: ${event.message}`,
                streaming: false,
              }));
              break;

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
        updateAssistant((m) => ({
          ...m,
          content: `Error: ${err instanceof Error ? err.message : "Unknown error"}`,
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

  // Auto-expand running agents by computing expanded set from state + running agents
  const [manualExpanded, setManualExpanded] = useState<Set<string>>(new Set());

  // Derive expandedAgents: manual toggles + auto-expand running agents during streaming
  const expandedAgents = (() => {
    const expanded = new Set(manualExpanded);
    if (msg.streaming) {
      msg.agents.forEach((a, i) => {
        if (a.status === "running") expanded.add(`${a.agent}-${i}`);
      });
    }
    return expanded;
  })();

  const toggleAgent = (agent: string) => {
    setManualExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      return next;
    });
  };

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
      className={`flex justify-start cursor-pointer transition-all ${isFocused ? "ring-1 ring-accent-primary/40 rounded-lg" : ""}`}
      onClick={handleFocus}
    >
      <div className="max-w-[95%] w-full space-y-2">
        {/* Agent pipeline visualization */}
        {msg.agents.length > 0 && (
          <div className="space-y-1">
            {msg.agents.map((agent, i) => (
              <AgentCard
                key={`${agent.agent}-${i}`}
                agent={agent}
                expanded={expandedAgents.has(`${agent.agent}-${i}`)}
                onToggle={() => toggleAgent(`${agent.agent}-${i}`)}
              />
            ))}
          </div>
        )}

        {/* Plan badge */}
        {msg.plan && (
          <div className="flex items-center gap-2 px-2">
            <span className="text-[10px] uppercase tracking-wider text-t-tertiary">
              Plan
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-j-secondary-soft text-j-secondary border border-j-secondary/30">
              {msg.plan.goal}
            </span>
            {msg.plan.steps.length > 0 && (
              <span className="text-[10px] text-t-muted">
                {msg.plan.steps.length} step{msg.plan.steps.length !== 1 ? "s" : ""}
              </span>
            )}
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

        {/* Trace ID */}
        {msg.traceId && !msg.streaming && (
          <p className="text-[10px] text-t-muted px-2">
            trace: {msg.traceId}
          </p>
        )}
      </div>
    </div>
  );
}

function AgentCard({
  agent,
  expanded,
  onToggle,
}: {
  agent: AgentStep;
  expanded: boolean;
  onToggle: () => void;
}) {
  const statusColor =
    agent.status === "running"
      ? "text-j-primary"
      : agent.status === "done"
        ? "text-j-success"
        : "text-j-error";

  const statusIcon =
    agent.status === "running"
      ? "\u25CB"
      : agent.status === "done"
        ? "\u2713"
        : "\u2717";

  return (
    <div className="rounded border border-b-primary bg-surface-1 text-xs">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-surface-2 transition-colors cursor-pointer"
      >
        <span className={statusColor}>{statusIcon}</span>
        <span className="font-medium text-t-primary capitalize">
          {agent.agent}
        </span>
        {agent.model && (
          <span className="text-t-muted">
            {agent.model.replace("claude-", "").replace(/-\d+$/, "")}
          </span>
        )}
        {agent.costUsd != null && agent.costUsd > 0 && (
          <span className="text-j-success/70">
            ${agent.costUsd < 0.01 ? agent.costUsd.toFixed(4) : agent.costUsd.toFixed(3)}
          </span>
        )}
        {agent.latencyMs != null && (
          <span className="text-t-muted ml-auto">
            {agent.latencyMs > 1000
              ? `${(agent.latencyMs / 1000).toFixed(1)}s`
              : `${agent.latencyMs}ms`}
          </span>
        )}
        <span className="text-t-muted ml-auto">
          {expanded ? "\u25B2" : "\u25BC"}
        </span>
      </button>

      {expanded && (
        <div className="px-2.5 pb-2 space-y-1.5 border-t border-b-primary">
          {/* Extended Thinking (real Claude thinking) */}
          {agent.realThinking.length > 0 && (
            <div className="mt-1.5">
              <p className="text-j-warning/70 text-[10px] uppercase tracking-wider mb-0.5">
                Extended Thinking
              </p>
              <div className="text-j-warning/60 bg-j-warning-soft border border-j-warning/20 rounded px-2 py-1 max-h-40 overflow-y-auto">
                {agent.realThinking.map((t, i) => (
                  <span key={i} className="whitespace-pre-wrap">
                    {t.length > 1000 ? t.slice(0, 1000) + "..." : t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Agent reasoning (non-thinking text blocks) */}
          {agent.thinking.length > 0 && (
            <div className="mt-1.5">
              <p className="text-t-tertiary text-[10px] uppercase tracking-wider mb-0.5">
                Reasoning
              </p>
              <div className="text-t-secondary bg-surface-0 rounded px-2 py-1 max-h-32 overflow-y-auto">
                {agent.thinking.map((t, i) => (
                  <p key={i} className="whitespace-pre-wrap">
                    {t.length > 500 ? t.slice(0, 500) + "..." : t}
                  </p>
                ))}
              </div>
            </div>
          )}

          {/* Streaming text (live output) */}
          {agent.status === "running" && agent.streamingText && (
            <div className="mt-1.5">
              <p className="text-j-primary/70 text-[10px] uppercase tracking-wider mb-0.5">
                Streaming
              </p>
              <div className="text-j-primary/60 bg-j-primary-soft border border-j-primary/20 rounded px-2 py-1 max-h-32 overflow-y-auto">
                <p className="whitespace-pre-wrap">{agent.streamingText}</p>
              </div>
            </div>
          )}

          {/* Token usage + cost stats */}
          {(agent.inputTokens || agent.outputTokens) && (
            <div className="text-t-muted mt-1 space-y-0.5">
              <p>
                Tokens: {agent.inputTokens?.toLocaleString()} in /{" "}
                {agent.outputTokens?.toLocaleString()} out
                {agent.costUsd != null && agent.costUsd > 0 && (
                  <span className="text-j-success/60 ml-2">
                    ${agent.costUsd < 0.01 ? agent.costUsd.toFixed(4) : agent.costUsd.toFixed(3)}
                  </span>
                )}
              </p>
              {(agent.cacheCreationTokens != null && agent.cacheCreationTokens > 0 ||
                agent.cacheReadTokens != null && agent.cacheReadTokens > 0) && (
                <p className="text-j-info/60">
                  Cache: {agent.cacheCreationTokens?.toLocaleString() || 0} write /{" "}
                  {agent.cacheReadTokens?.toLocaleString() || 0} read
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat, type ChatSSEEvent, type ConversationMessage } from "@/lib/api";
import { CommandInput } from "./command-input";

interface AgentStep {
  agent: string;
  model?: string;
  status: "running" | "done" | "error";
  thinking: string[];
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
  latencyMs?: number;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  traceId?: string;
  decision?: Record<string, unknown>;
  agents: AgentStep[];
  streaming?: boolean;
}

interface ChatPanelProps {
  conversationId?: string | null;
  initialMessages?: ConversationMessage[];
  onConversationCreated?: (id: string) => void;
  onMessageSent?: () => void;
}

function backendMessagesToChat(messages: ConversationMessage[]): ChatMessage[] {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      id: m.message_id,
      role: m.role as "user" | "assistant",
      content: m.content,
      timestamp: m.created_at || new Date().toISOString(),
      traceId: (m.metadata_?.trace_id as string) || undefined,
      agents: [],
    }));
}

export function ChatPanel({
  conversationId,
  initialMessages,
  onConversationCreated,
  onMessageSent,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeConvoRef = useRef<string | null>(conversationId ?? null);

  // Sync conversationId to ref
  useEffect(() => {
    activeConvoRef.current = conversationId ?? null;
  }, [conversationId]);

  // Load initial messages when conversation changes
  useEffect(() => {
    if (initialMessages) {
      setMessages(backendMessagesToChat(initialMessages));
    } else {
      setMessages([]);
    }
  }, [initialMessages]);

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

    const assistantId = crypto.randomUUID();
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
                    ? { ...a, thinking: [...a.thinking, event.text || ""] }
                    : a
                ),
              }));
              scrollToBottom();
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
                            input: event.input || {},
                          },
                        ],
                      }
                    : a
                ),
              }));
              scrollToBottom();
              break;

            case "tool_result":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? {
                        ...a,
                        toolCalls: a.toolCalls.map((tc, i) =>
                          i === a.toolCalls.length - 1 && tc.tool === event.tool
                            ? {
                                ...tc,
                                result: event.result,
                                blocked: event.blocked,
                                latencyMs: event.latency_ms,
                              }
                            : tc
                        ),
                      }
                    : a
                ),
              }));
              scrollToBottom();
              break;

            case "decision":
              updateAssistant((m) => ({
                ...m,
                decision: event.decision,
              }));
              break;

            case "agent_done":
              updateAssistant((m) => ({
                ...m,
                agents: m.agents.map((a) =>
                  a.agent === event.agent && a.status === "running"
                    ? {
                        ...a,
                        status: "done",
                        text: event.text,
                        inputTokens: event.input_tokens,
                        outputTokens: event.output_tokens,
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
        activeConvoRef.current
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
          <div className="text-neutral-500 text-sm text-center mt-8 space-y-2">
            <p className="text-lg text-neutral-400">Talk to Jarvis</p>
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
      <div className="border-t border-neutral-800 p-4">
        <CommandInput onSubmit={handleSubmit} disabled={loading} />
      </div>
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-blue-600 text-white">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}

function AssistantMessage({ msg }: { msg: ChatMessage }) {
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set());

  const toggleAgent = (agent: string) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      return next;
    });
  };

  return (
    <div className="flex justify-start">
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

        {/* Decision badge */}
        {msg.decision && (
          <div className="flex items-center gap-2 px-2">
            <span className="text-[10px] uppercase tracking-wider text-neutral-500">
              Decision
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-blue-900/50 text-blue-300 border border-blue-800">
              {String(msg.decision.decision || "unknown")}
            </span>
          </div>
        )}

        {/* Final response */}
        {msg.content ? (
          <div className="rounded-lg px-3 py-2 text-sm bg-neutral-800 text-neutral-200">
            <p className="whitespace-pre-wrap">{msg.content}</p>
          </div>
        ) : msg.streaming ? (
          <div className="rounded-lg px-3 py-2 text-sm bg-neutral-800 text-neutral-400">
            <span className="inline-flex gap-1">
              <span className="animate-pulse">Processing</span>
              <span className="animate-bounce" style={{ animationDelay: "0ms" }}>.</span>
              <span className="animate-bounce" style={{ animationDelay: "150ms" }}>.</span>
              <span className="animate-bounce" style={{ animationDelay: "300ms" }}>.</span>
            </span>
          </div>
        ) : null}

        {/* Trace ID */}
        {msg.traceId && !msg.streaming && (
          <p className="text-[10px] text-neutral-600 px-2">
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
      ? "text-yellow-400"
      : agent.status === "done"
        ? "text-green-400"
        : "text-red-400";

  const statusIcon =
    agent.status === "running"
      ? "\u25CB"
      : agent.status === "done"
        ? "\u2713"
        : "\u2717";

  return (
    <div className="rounded border border-neutral-800 bg-neutral-900/50 text-xs">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-neutral-800/50 transition-colors cursor-pointer"
      >
        <span className={statusColor}>{statusIcon}</span>
        <span className="font-medium text-neutral-300 capitalize">
          {agent.agent}
        </span>
        {agent.model && (
          <span className="text-neutral-600">
            {agent.model.replace("claude-", "").replace(/-\d+$/, "")}
          </span>
        )}
        {agent.toolCalls.length > 0 && (
          <span className="text-neutral-500">
            {agent.toolCalls.length} tool{agent.toolCalls.length !== 1 ? "s" : ""}
          </span>
        )}
        {agent.latencyMs != null && (
          <span className="text-neutral-600 ml-auto">
            {agent.latencyMs > 1000
              ? `${(agent.latencyMs / 1000).toFixed(1)}s`
              : `${agent.latencyMs}ms`}
          </span>
        )}
        <span className="text-neutral-600 ml-auto">
          {expanded ? "\u25B2" : "\u25BC"}
        </span>
      </button>

      {expanded && (
        <div className="px-2.5 pb-2 space-y-1.5 border-t border-neutral-800">
          {/* Thinking */}
          {agent.thinking.length > 0 && (
            <div className="mt-1.5">
              <p className="text-neutral-500 text-[10px] uppercase tracking-wider mb-0.5">
                Thinking
              </p>
              <div className="text-neutral-400 bg-neutral-950 rounded px-2 py-1 max-h-32 overflow-y-auto">
                {agent.thinking.map((t, i) => (
                  <p key={i} className="whitespace-pre-wrap">
                    {t.length > 500 ? t.slice(0, 500) + "..." : t}
                  </p>
                ))}
              </div>
            </div>
          )}

          {/* Tool calls */}
          {agent.toolCalls.map((tc, i) => (
            <div key={i} className="mt-1">
              <div className="flex items-center gap-1.5">
                <span className="text-purple-400">{"\u2192"}</span>
                <span className="text-purple-300 font-mono">{tc.tool}</span>
                {tc.blocked && (
                  <span className="text-red-400 text-[10px]">BLOCKED</span>
                )}
                {tc.latencyMs != null && (
                  <span className="text-neutral-600">{tc.latencyMs}ms</span>
                )}
              </div>
              {Object.keys(tc.input).length > 0 && (
                <pre className="text-neutral-500 bg-neutral-950 rounded px-2 py-0.5 mt-0.5 overflow-x-auto max-h-20 overflow-y-auto">
                  {JSON.stringify(tc.input, null, 2)}
                </pre>
              )}
              {tc.result !== undefined && (
                <pre className="text-neutral-400 bg-neutral-950 rounded px-2 py-0.5 mt-0.5 overflow-x-auto max-h-20 overflow-y-auto">
                  {typeof tc.result === "string"
                    ? tc.result.slice(0, 300)
                    : JSON.stringify(tc.result, null, 2)?.slice(0, 300)}
                </pre>
              )}
            </div>
          ))}

          {/* Token usage */}
          {(agent.inputTokens || agent.outputTokens) && (
            <p className="text-neutral-600 mt-1">
              Tokens: {agent.inputTokens?.toLocaleString()} in /{" "}
              {agent.outputTokens?.toLocaleString()} out
            </p>
          )}
        </div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { type PlanOutput } from "@/lib/api";

export interface AgentStep {
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

interface AgentTraceProps {
  agents: AgentStep[];
  plan?: PlanOutput | null;
  streaming: boolean;
}

/**
 * Renders the agent pipeline ("how Jarvis answered") for an assistant message:
 * per-agent cards with model, cost, thinking and the plan badge. Owns the
 * per-agent expand/collapse state, including auto-expanding running agents
 * while a response is streaming.
 */
export function AgentTrace({ agents, plan, streaming }: AgentTraceProps) {
  const [manualExpanded, setManualExpanded] = useState<Set<string>>(new Set());

  if (agents.length === 0) return null;

  // Manual toggles, plus auto-expand any running agent during streaming.
  const expandedAgents = new Set(manualExpanded);
  if (streaming) {
    agents.forEach((a, i) => {
      if (a.status === "running") expandedAgents.add(`${a.agent}-${i}`);
    });
  }

  const toggleAgent = (key: string) => {
    setManualExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <>
      {/* Agent pipeline visualization */}
      <div className="space-y-1">
        {agents.map((agent, i) => (
          <AgentCard
            key={`${agent.agent}-${i}`}
            agent={agent}
            expanded={expandedAgents.has(`${agent.agent}-${i}`)}
            onToggle={() => toggleAgent(`${agent.agent}-${i}`)}
          />
        ))}
      </div>

      {/* Plan badge */}
      {plan && (
        <div className="flex items-center gap-2 px-2">
          <span className="text-[10px] uppercase tracking-wider text-t-tertiary">Plan</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-j-secondary-soft text-j-secondary border border-j-secondary/30">
            {plan.goal}
          </span>
          {plan.steps.length > 0 && (
            <span className="text-[10px] text-t-muted">
              {plan.steps.length} step{plan.steps.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      )}
    </>
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
    agent.status === "running" ? "○" : agent.status === "done" ? "✓" : "✗";

  return (
    <div className="rounded border border-b-primary bg-surface-1 text-xs">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-surface-2 transition-colors cursor-pointer"
      >
        <span className={statusColor}>{statusIcon}</span>
        <span className="font-medium text-t-primary capitalize">{agent.agent}</span>
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
        <span className="text-t-muted ml-auto">{expanded ? "▲" : "▼"}</span>
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
              {((agent.cacheCreationTokens != null && agent.cacheCreationTokens > 0) ||
                (agent.cacheReadTokens != null && agent.cacheReadTokens > 0)) && (
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

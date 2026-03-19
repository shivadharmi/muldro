"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchAgents, updateAgent, toggleAgent, type AgentRecord } from "@/lib/api";
import { useToast } from "@/components/ui/toast";

export default function AgentsPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editTemp, setEditTemp] = useState("0.3");

  const { data, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: fetchAgents,
    refetchInterval: 60_000,
  });

  const agents: AgentRecord[] = data || [];

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleAgent(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
    onError: (err) => addToast(`Failed to toggle agent: ${err.message}`, "error"),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<AgentRecord> }) =>
      updateAgent(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setEditingId(null);
      addToast("Agent updated", "success");
    },
    onError: (err) => addToast(`Failed to update agent: ${err.message}`, "error"),
  });

  const startEdit = (agent: AgentRecord) => {
    setEditingId(agent.agent_id);
    setEditPrompt(agent.system_prompt || "");
    setEditTemp(String(agent.temperature));
  };

  const handleSave = (agentId: string) => {
    updateMut.mutate({
      id: agentId,
      updates: {
        system_prompt: editPrompt,
        temperature: parseFloat(editTemp),
      },
    });
  };

  const tierColor = (tier: string) => {
    if (tier === "opus") return "purple" as const;
    if (tier === "haiku") return "green" as const;
    return "blue" as const;
  };

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Agents"
        subtitle="Manage sub-agent configurations, prompts, and model tiers"
        variant="config"
      />

      {isLoading && (
        <div className="text-center py-12 text-t-tertiary text-sm">Loading...</div>
      )}

      {!isLoading && agents.length === 0 && (
        <div className="text-center py-12 text-t-tertiary text-sm">
          No agents found. Agents are seeded on first startup.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map((agent) => {
          const a = agent as AgentRecord & Record<string, unknown>;
          const callsToday = typeof a.calls_today === "number" ? a.calls_today : null;
          const successRate = typeof a.success_rate === "number" ? a.success_rate : null;
          const avgLatency = typeof a.avg_latency_ms === "number" ? a.avg_latency_ms : null;
          const lastInvoked = typeof a.last_invoked_at === "string" ? a.last_invoked_at : null;

          return (
            <Card key={agent.agent_id}>
              <div className="p-4 space-y-3">
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <p className="text-sm font-medium text-t-primary">
                        {agent.display_name || agent.name}
                      </p>
                      <Badge variant={agent.enabled ? "green" : "default"}>
                        {agent.enabled ? "Active" : "Disabled"}
                      </Badge>
                      <Badge variant={tierColor(agent.model_tier)}>
                        {agent.model_tier}
                      </Badge>
                    </div>
                    {(a.description || agent.system_prompt) && (
                      <p className="text-xs text-t-secondary mt-0.5 line-clamp-2">
                        {String(a.description || agent.system_prompt || "").slice(0, 120)}
                        {String(a.description || agent.system_prompt || "").length > 120 ? "..." : ""}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() =>
                        editingId === agent.agent_id
                          ? setEditingId(null)
                          : startEdit(agent)
                      }
                      className="text-xs text-t-secondary hover:text-t-primary"
                    >
                      {editingId === agent.agent_id ? "Cancel" : "Edit"}
                    </button>
                    <button
                      onClick={() =>
                        toggleMut.mutate({
                          id: agent.agent_id,
                          enabled: !agent.enabled,
                        })
                      }
                      className="text-xs text-t-secondary hover:text-t-primary"
                    >
                      {agent.enabled ? "Disable" : "Enable"}
                    </button>
                  </div>
                </div>

                {/* Performance stats */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <p className="text-t-muted">Calls today</p>
                    <p className="text-t-primary font-medium">{callsToday ?? "-"}</p>
                  </div>
                  <div>
                    <p className="text-t-muted">Success rate</p>
                    <p className="text-t-primary font-medium">
                      {successRate !== null ? `${Math.round(successRate * 100)}%` : "-"}
                    </p>
                  </div>
                  <div>
                    <p className="text-t-muted">Avg latency</p>
                    <p className="text-t-primary font-medium">
                      {avgLatency !== null ? `${Math.round(avgLatency)}ms` : "-"}
                    </p>
                  </div>
                  <div>
                    <p className="text-t-muted">Last invoked</p>
                    <p className="text-t-primary font-medium text-[10px]">
                      {lastInvoked ? new Date(lastInvoked).toLocaleString() : "-"}
                    </p>
                  </div>
                </div>

                <div className="text-xs text-t-tertiary">
                  temp {agent.temperature} &middot; max {agent.max_tokens} tokens
                  {agent.tool_scope && agent.tool_scope.length > 0 && (
                    <span className="ml-1">
                      &middot; {agent.tool_scope.length} tools
                    </span>
                  )}
                </div>

                {editingId === agent.agent_id && (
                  <div className="space-y-2 border-t border-b-primary pt-3">
                    <div className="space-y-1">
                      <label className="text-xs text-t-secondary">System Prompt</label>
                      <textarea
                        value={editPrompt}
                        onChange={(e) => setEditPrompt(e.target.value)}
                        rows={4}
                        className="w-full rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-1 focus:ring-j-ring font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-t-secondary">Temperature</label>
                      <input
                        type="number"
                        min="0"
                        max="1"
                        step="0.1"
                        value={editTemp}
                        onChange={(e) => setEditTemp(e.target.value)}
                        className="w-32 rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                      />
                    </div>
                    <button
                      onClick={() => handleSave(agent.agent_id)}
                      disabled={updateMut.isPending}
                      className="px-4 py-2 rounded-lg bg-j-primary text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
                    >
                      {updateMut.isPending ? "Saving..." : "Save"}
                    </button>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

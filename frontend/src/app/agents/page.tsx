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
    <div className="p-6 space-y-6">
      <PageHeader
        title="Agents"
        subtitle="Manage sub-agent configurations, prompts, and model tiers"
      />

      {isLoading && (
        <div className="text-center py-12 text-neutral-500 text-sm">Loading...</div>
      )}

      {!isLoading && agents.length === 0 && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          No agents found. Agents are seeded on first startup.
        </div>
      )}

      <div className="space-y-3">
        {agents.map((agent) => (
          <Card key={agent.agent_id}>
            <div className="p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-medium text-white">
                      {agent.display_name || agent.name}
                    </p>
                    <Badge variant={agent.enabled ? "green" : "default"}>
                      {agent.enabled ? "Active" : "Disabled"}
                    </Badge>
                    <Badge variant={tierColor(agent.model_tier)}>
                      {agent.model_tier}
                    </Badge>
                  </div>
                  <p className="text-xs text-neutral-500">
                    {agent.name} &middot; temp {agent.temperature} &middot; max {agent.max_tokens} tokens
                  </p>
                  {agent.tool_scope && agent.tool_scope.length > 0 && (
                    <p className="text-[10px] text-neutral-600 mt-1">
                      Tools: {agent.tool_scope.slice(0, 5).join(", ")}
                      {agent.tool_scope.length > 5 && ` +${agent.tool_scope.length - 5} more`}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() =>
                      editingId === agent.agent_id
                        ? setEditingId(null)
                        : startEdit(agent)
                    }
                    className="text-xs text-neutral-400 hover:text-white"
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
                    className="text-xs text-neutral-400 hover:text-white"
                  >
                    {agent.enabled ? "Disable" : "Enable"}
                  </button>
                </div>
              </div>

              {editingId === agent.agent_id && (
                <div className="mt-3 space-y-2 border-t border-neutral-800 pt-3">
                  <div className="space-y-1">
                    <label className="text-xs text-neutral-400">System Prompt</label>
                    <textarea
                      value={editPrompt}
                      onChange={(e) => setEditPrompt(e.target.value)}
                      rows={4}
                      className="w-full rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-neutral-400">Temperature</label>
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.1"
                      value={editTemp}
                      onChange={(e) => setEditTemp(e.target.value)}
                      className="w-32 rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                  <button
                    onClick={() => handleSave(agent.agent_id)}
                    disabled={updateMut.isPending}
                    className="px-4 py-2 rounded-lg bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {updateMut.isPending ? "Saving..." : "Save"}
                  </button>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

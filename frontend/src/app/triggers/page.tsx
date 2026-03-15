"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchTriggers, createTrigger, deleteTrigger, toggleTrigger } from "@/lib/api";

export default function TriggersPage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [eventType, setEventType] = useState("");
  const [actionType, setActionType] = useState("notify");

  const { data, isLoading } = useQuery({
    queryKey: ["triggers"],
    queryFn: fetchTriggers,
    refetchInterval: 30_000,
  });

  const triggers = data?.triggers || [];

  const createMut = useMutation({
    mutationFn: (input: Record<string, unknown>) => createTrigger(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
      setShowForm(false);
      setName("");
      setEventType("");
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteTrigger(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["triggers"] }),
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleTrigger(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["triggers"] }),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !eventType.trim()) return;
    createMut.mutate({
      name,
      conditions: { event_type: eventType },
      action_type: actionType,
      action_config: {},
    });
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <PageHeader
          title="Triggers"
          subtitle="Reactive automation rules that fire on events"
        />
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-3 py-1.5 rounded-lg bg-blue-600 text-sm font-medium text-white hover:bg-blue-700"
        >
          {showForm ? "Cancel" : "New Trigger"}
        </button>
      </div>

      {showForm && (
        <Card>
          <form onSubmit={handleCreate} className="p-4 space-y-3">
            <div className="space-y-1">
              <label className="text-xs text-neutral-400">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Notify on PR reviews"
                className="w-full rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-neutral-400">Event Type</label>
              <input
                type="text"
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
                placeholder="e.g., pr_reviewed, email_received"
                className="w-full rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-neutral-400">Action</label>
              <select
                value={actionType}
                onChange={(e) => setActionType(e.target.value)}
                className="w-full rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="notify">Notify</option>
                <option value="plan">Create Plan</option>
                <option value="escalate">Escalate</option>
                <option value="procedure">Run Procedure</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={createMut.isPending}
              className="px-4 py-2 rounded-lg bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {createMut.isPending ? "Creating..." : "Create Trigger"}
            </button>
          </form>
        </Card>
      )}

      {isLoading && (
        <div className="text-center py-12 text-neutral-500 text-sm">Loading...</div>
      )}

      {!isLoading && triggers.length === 0 && !showForm && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          No triggers configured yet
        </div>
      )}

      <div className="space-y-3">
        {triggers.map((trigger: Record<string, unknown>) => {
          const id = trigger.trigger_id as string;
          const enabled = trigger.enabled !== false;
          const conditions = trigger.conditions as Record<string, unknown> | undefined;

          return (
            <Card key={id}>
              <div className="p-4 flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-medium text-white">
                      {(trigger.name as string) || "Untitled"}
                    </p>
                    <Badge variant={enabled ? "green" : "default"}>
                      {enabled ? "Active" : "Disabled"}
                    </Badge>
                    <Badge variant="blue">
                      {(trigger.action_type as string) || "notify"}
                    </Badge>
                  </div>
                  {conditions && (
                    <p className="text-xs text-neutral-500">
                      When: {conditions.event_type as string || "any"}
                      {conditions.source ? ` from ${conditions.source}` : ""}
                    </p>
                  )}
                  {trigger.fire_count !== undefined && (
                    <p className="text-[10px] text-neutral-600 mt-1">
                      Fired {trigger.fire_count as number} times
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() =>
                      toggleMut.mutate({ id, enabled: !enabled })
                    }
                    className="text-xs text-neutral-400 hover:text-white"
                  >
                    {enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm("Delete this trigger?")) {
                        deleteMut.mutate(id);
                      }
                    }}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

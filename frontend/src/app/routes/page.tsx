"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchRoutes, updateRoute, deleteRoute } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import type { AgentRoute } from "@/lib/types";

export default function RoutesPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPriority, setEditPriority] = useState("100");
  const [editEnabled, setEditEnabled] = useState(true);

  const { data, isLoading } = useQuery({
    queryKey: ["routes"],
    queryFn: fetchRoutes,
    refetchInterval: 60_000,
  });

  const routes: AgentRoute[] = data || [];

  const updateMut = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<AgentRoute> }) =>
      updateRoute(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routes"] });
      setEditingId(null);
      addToast("Route updated", "success");
    },
    onError: (err) => addToast(`Failed to update route: ${err.message}`, "error"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteRoute(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routes"] });
      addToast("Route deleted", "success");
    },
    onError: (err) => addToast(`Failed to delete route: ${err.message}`, "error"),
  });

  const startEdit = (route: AgentRoute) => {
    setEditingId(route.route_id);
    setEditPriority(String(route.priority));
    setEditEnabled(route.enabled);
  };

  const handleSave = (routeId: string) => {
    updateMut.mutate({
      id: routeId,
      updates: {
        priority: parseInt(editPriority, 10),
        enabled: editEnabled,
      },
    });
  };

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Routes"
        subtitle="Dynamic routing rules that map decisions to agent pipelines"
      />

      {isLoading && (
        <div className="text-center py-12 text-neutral-500 text-sm">Loading...</div>
      )}

      {!isLoading && routes.length === 0 && (
        <div className="text-center py-12 text-neutral-500 text-sm">
          No routes found. Routes are seeded on first startup.
        </div>
      )}

      <div className="space-y-3">
        {routes.map((route) => (
          <Card key={route.route_id}>
            <div className="p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-medium text-white">{route.name}</p>
                    <Badge variant={route.enabled ? "green" : "default"}>
                      {route.enabled ? "Active" : "Disabled"}
                    </Badge>
                    <Badge variant="blue">{route.decision_type}</Badge>
                  </div>
                  {route.description && (
                    <p className="text-xs text-neutral-400 mb-1">{route.description}</p>
                  )}
                  <p className="text-xs text-neutral-500">
                    Priority {route.priority} &middot; Weight {route.weight}
                  </p>
                  {route.agent_pipeline.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {route.agent_pipeline.map((step, i) => (
                        <Badge key={i} variant="default">
                          {(step as Record<string, unknown>).agent as string || `Step ${i + 1}`}
                        </Badge>
                      ))}
                    </div>
                  )}
                  {route.keywords && route.keywords.length > 0 && (
                    <p className="text-[10px] text-neutral-600 mt-1">
                      Keywords: {route.keywords.join(", ")}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() =>
                      editingId === route.route_id
                        ? setEditingId(null)
                        : startEdit(route)
                    }
                    className="text-xs text-neutral-400 hover:text-white"
                  >
                    {editingId === route.route_id ? "Cancel" : "Edit"}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete route "${route.name}"?`)) {
                        deleteMut.mutate(route.route_id);
                      }
                    }}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    Delete
                  </button>
                </div>
              </div>

              {editingId === route.route_id && (
                <div className="mt-3 space-y-2 border-t border-neutral-800 pt-3">
                  <div className="flex gap-4">
                    <div className="space-y-1">
                      <label className="text-xs text-neutral-400">Priority</label>
                      <input
                        type="number"
                        min="1"
                        max="1000"
                        value={editPriority}
                        onChange={(e) => setEditPriority(e.target.value)}
                        className="w-24 rounded bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-neutral-400">Enabled</label>
                      <div className="pt-1">
                        <input
                          type="checkbox"
                          checked={editEnabled}
                          onChange={(e) => setEditEnabled(e.target.checked)}
                          className="rounded"
                        />
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => handleSave(route.route_id)}
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

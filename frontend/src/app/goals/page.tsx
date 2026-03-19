"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchGoals, createGoal, updateGoal, deleteGoal } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { GoalCard } from "@/components/goals/goal-card";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import type { GoalCreateInput } from "@/lib/types";

export default function GoalsPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [showForm, setShowForm] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");

  const { data: goals = [], isLoading } = useQuery({
    queryKey: ["goals", statusFilter],
    queryFn: () => fetchGoals(statusFilter || undefined),
    refetchInterval: 30_000,
  });

  const createMut = useMutation({
    mutationFn: (input: GoalCreateInput) => createGoal(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      setShowForm(false);
      setTitle("");
      setDescription("");
      setPriority("medium");
      addToast("Goal created", "success");
    },
    onError: (err) => addToast(`Failed to create goal: ${err.message}`, "error"),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<GoalCreateInput & { status: string; progress: number }> }) =>
      updateGoal(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      addToast("Goal updated", "success");
    },
    onError: (err) => addToast(`Failed to update goal: ${err.message}`, "error"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteGoal(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      addToast("Goal deleted", "success");
    },
    onError: (err) => addToast(`Failed to delete goal: ${err.message}`, "error"),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    createMut.mutate({ title: title.trim(), description: description.trim() || undefined, priority });
  }

  return (
    <div className="p-4 sm:p-6">
      <PageHeader
        title="Goals"
        subtitle="Track and manage your goals"
        variant="collection"
        actions={
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary"
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="paused">Paused</option>
            </select>
            <button
              onClick={() => setShowForm(!showForm)}
              className="bg-j-primary hover:bg-j-primary-hover text-j-primary-fg text-sm px-3 py-1.5 rounded transition-colors"
            >
              {showForm ? "Cancel" : "New Goal"}
            </button>
          </div>
        }
      />

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-surface-1 border border-b-primary rounded-lg p-4 mb-6 space-y-3">
          <div>
            <label className="block text-xs text-t-secondary mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Goal title"
              className="w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary placeholder:text-t-muted"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-t-secondary mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={2}
              className="w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary placeholder:text-t-muted resize-none"
            />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <label className="block text-xs text-t-secondary mb-1">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </div>
            <div className="flex-1" />
            <button
              type="submit"
              disabled={createMut.isPending}
              className="bg-j-primary hover:bg-j-primary-hover disabled:opacity-50 text-j-primary-fg text-sm px-4 py-1.5 rounded transition-colors mt-4"
            >
              {createMut.isPending ? "Creating..." : "Create Goal"}
            </button>
          </div>
        </form>
      )}

      {isLoading ? (
        <p className="text-t-tertiary text-sm">Loading...</p>
      ) : goals.length === 0 ? (
        <EmptyState title="No goals" description="Create a goal to start tracking progress" />
      ) : (
        <div className="space-y-3">
          {goals.map((goal) => (
            <GoalCard
              key={goal.goal_id}
              goal={goal}
              onUpdate={(id, input) => updateMut.mutate({ id, input })}
              onDelete={(id) => deleteMut.mutate(id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

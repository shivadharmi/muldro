"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchGoals, createGoal, updateGoal, deleteGoal } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { GoalCard } from "@/components/goals/goal-card";
import { EmptyState } from "@/components/ui/empty-state";
import type { GoalCreateInput } from "@/lib/types";

export default function GoalsPage() {
  const queryClient = useQueryClient();
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
    },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<GoalCreateInput & { status: string; progress: number }> }) =>
      updateGoal(id, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["goals"] }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteGoal(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["goals"] }),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    createMut.mutate({ title: title.trim(), description: description.trim() || undefined, priority });
  }

  return (
    <div className="p-6">
      <PageHeader
        title="Goals"
        subtitle="Track and manage your goals"
        actions={
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="paused">Paused</option>
            </select>
            <button
              onClick={() => setShowForm(!showForm)}
              className="bg-blue-600 hover:bg-blue-700 text-white text-sm px-3 py-1.5 rounded transition-colors"
            >
              {showForm ? "Cancel" : "New Goal"}
            </button>
          </div>
        }
      />

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 mb-6 space-y-3">
          <div>
            <label className="block text-xs text-neutral-400 mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Goal title"
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200 placeholder:text-neutral-600"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-neutral-400 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={2}
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200 placeholder:text-neutral-600 resize-none"
            />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <label className="block text-xs text-neutral-400 mb-1">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
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
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm px-4 py-1.5 rounded transition-colors mt-4"
            >
              {createMut.isPending ? "Creating..." : "Create Goal"}
            </button>
          </div>
        </form>
      )}

      {isLoading ? (
        <p className="text-neutral-500 text-sm">Loading...</p>
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

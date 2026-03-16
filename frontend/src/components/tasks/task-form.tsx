"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createTask, fetchGoals } from "@/lib/api";
import type { StandaloneTaskCreateInput } from "@/lib/types";

export function TaskForm({ onClose }: { onClose?: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [taskType, setTaskType] = useState("general");
  const [priority, setPriority] = useState("medium");
  const [goalId, setGoalId] = useState("");

  const { data: goals = [] } = useQuery({
    queryKey: ["goals"],
    queryFn: () => fetchGoals(),
  });

  const createMut = useMutation({
    mutationFn: (input: StandaloneTaskCreateInput) => createTask(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      setTitle("");
      setDescription("");
      setTaskType("general");
      setPriority("medium");
      setGoalId("");
      onClose?.();
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    createMut.mutate({
      title: title.trim(),
      description: description.trim() || undefined,
      task_type: taskType,
      priority,
      goal_id: goalId || undefined,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 space-y-3">
      <div>
        <label className="block text-xs text-neutral-400 mb-1">Title</label>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Task title"
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

      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-neutral-400 mb-1">Type</label>
          <select
            value={taskType}
            onChange={(e) => setTaskType(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="general">General</option>
            <option value="research">Research</option>
            <option value="communication">Communication</option>
            <option value="automation">Automation</option>
            <option value="review">Review</option>
          </select>
        </div>

        <div>
          <label className="block text-xs text-neutral-400 mb-1">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </div>

        <div>
          <label className="block text-xs text-neutral-400 mb-1">Goal</label>
          <select
            value={goalId}
            onChange={(e) => setGoalId(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="">None</option>
            {goals.map((g) => (
              <option key={g.goal_id} value={g.goal_id}>
                {g.title}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-200 text-sm px-3 py-1.5 rounded transition-colors"
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={createMut.isPending}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm px-4 py-1.5 rounded transition-colors"
        >
          {createMut.isPending ? "Creating..." : "Create Task"}
        </button>
      </div>
    </form>
  );
}

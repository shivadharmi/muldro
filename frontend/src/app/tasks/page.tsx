"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchStandaloneTasks,
  fetchGoals,
  createTask,
  startTask,
  cancelTask,
  resumeTask,
} from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeAgo } from "@/components/ui/time-ago";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import type { StandaloneTask } from "@/lib/types";

const STATUS_OPTIONS = ["all", "pending", "running", "completed", "failed", "cancelled", "blocked"];
const PRIORITY_OPTIONS = ["all", "critical", "high", "medium", "low"];
const TASK_TYPE_OPTIONS = ["all", "action", "research", "approval", "notification"];

export default function TasksPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [dueAt, setDueAt] = useState("");
  const [goalId, setGoalId] = useState("");

  const [statusFilter, setStatusFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ["standalone-tasks", statusFilter, priorityFilter, typeFilter],
    queryFn: () =>
      fetchStandaloneTasks({
        status: statusFilter === "all" ? undefined : statusFilter,
        priority: priorityFilter === "all" ? undefined : priorityFilter,
        task_type: typeFilter === "all" ? undefined : typeFilter,
      }),
    refetchInterval: 30_000,
  });

  const { data: goals = [] } = useQuery({
    queryKey: ["goals-for-tasks"],
    queryFn: () => fetchGoals("active"),
  });

  const createMut = useMutation({
    mutationFn: () =>
      createTask({
        title,
        description: description || undefined,
        priority,
        due_at: dueAt || undefined,
        goal_id: goalId || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["standalone-tasks"] });
      setShowCreate(false);
      setTitle("");
      setDescription("");
      setPriority("medium");
      setDueAt("");
      setGoalId("");
      addToast("Task created", "success");
    },
    onError: (err) => addToast(`Failed: ${err.message}`, "error"),
  });

  const actionMut = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "cancel" | "resume" }) => {
      switch (action) {
        case "start": return startTask(id);
        case "cancel": return cancelTask(id);
        case "resume": return resumeTask(id);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["standalone-tasks"] }),
    onError: (err) => addToast(`Action failed: ${err.message}`, "error"),
  });

  return (
    <div className="p-4 sm:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <PageHeader title="Tasks" subtitle="Manage standalone tasks" variant="collection" />
        <Button onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? "Cancel" : "New Task"}
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardBody className="space-y-3">
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Task title"
              className="w-full rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-1 focus:ring-j-ring"
            />
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Description (optional)"
              rows={2}
              className="w-full rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-1 focus:ring-j-ring"
            />
            <div className="flex items-center gap-3 flex-wrap">
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
              <input
                type="datetime-local"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
                className="rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary"
              />
              {goals.length > 0 && (
                <select
                  value={goalId}
                  onChange={(e) => setGoalId(e.target.value)}
                  className="rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary"
                >
                  <option value="">No goal</option>
                  {goals.map((g) => (
                    <option key={g.goal_id} value={g.goal_id}>
                      {g.title}
                    </option>
                  ))}
                </select>
              )}
              <Button
                onClick={() => createMut.mutate()}
                disabled={!title.trim() || createMut.isPending}
              >
                {createMut.isPending ? "Creating..." : "Create Task"}
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded bg-surface-2 border border-b-primary px-3 py-1.5 text-sm text-t-primary"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All statuses" : s}
            </option>
          ))}
        </select>
        <div className="flex gap-1">
          {PRIORITY_OPTIONS.map((p) => (
            <button
              key={p}
              onClick={() => setPriorityFilter(p)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                priorityFilter === p
                  ? "bg-j-primary text-j-primary-fg"
                  : "bg-surface-2 text-t-secondary hover:text-t-primary"
              }`}
            >
              {p === "all" ? "All" : p}
            </button>
          ))}
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded bg-surface-2 border border-b-primary px-3 py-1.5 text-sm text-t-primary"
        >
          {TASK_TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>
              {t === "all" ? "All types" : t}
            </option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardBody>
                <div className="animate-pulse space-y-2">
                  <div className="h-4 w-48 bg-surface-2 rounded" />
                  <div className="h-3 w-32 bg-surface-2 rounded" />
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <EmptyState
          title="No tasks"
          description="Create a task or ask Jarvis to plan something."
        />
      ) : (
        <div className="space-y-2">
          {tasks.map((task: StandaloneTask) => (
            <TaskRow
              key={task.task_id}
              task={task}
              onAction={(action) => actionMut.mutate({ id: task.task_id, action })}
              isPending={actionMut.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({
  task,
  onAction,
  isPending,
}: {
  task: StandaloneTask;
  onAction: (action: "start" | "cancel" | "resume") => void;
  isPending: boolean;
}) {
  const canStart = task.status === "pending";
  const canCancel = task.status === "pending" || task.status === "running";
  const canResume = task.status === "blocked" || task.status === "failed";

  return (
    <Card>
      <CardBody>
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-t-primary truncate">{task.title}</h3>
              <Badge variant={statusVariant(task.status)}>{task.status}</Badge>
              <Badge variant={priorityVariant(task.priority)}>{task.priority}</Badge>
            </div>
            {task.description && (
              <p className="text-xs text-t-secondary mt-0.5 truncate">{task.description}</p>
            )}
            <div className="flex items-center gap-3 mt-1 text-[10px] text-t-muted">
              <span>{task.task_type}</span>
              <span>{task.source}</span>
              {task.assigned_agent && <span>Agent: {task.assigned_agent}</span>}
              {task.due_at && <span>Due: {new Date(task.due_at).toLocaleDateString()}</span>}
              {task.created_at && <TimeAgo date={task.created_at} />}
            </div>
          </div>
          <div className="flex items-center gap-1 ml-3 shrink-0">
            {canStart && (
              <Button size="sm" variant="primary" onClick={() => onAction("start")} disabled={isPending}>
                Start
              </Button>
            )}
            {canResume && (
              <Button size="sm" variant="secondary" onClick={() => onAction("resume")} disabled={isPending}>
                Resume
              </Button>
            )}
            {canCancel && (
              <Button size="sm" variant="danger" onClick={() => onAction("cancel")} disabled={isPending}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

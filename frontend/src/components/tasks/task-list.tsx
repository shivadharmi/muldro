"use client";

import type { Task } from "@/lib/types";
import { EmptyState } from "@/components/ui/empty-state";
import { TaskCard } from "./task-card";

export function TaskList({ tasks }: { tasks: Task[] }) {
  if (tasks.length === 0) {
    return <EmptyState title="No tasks" description="Tasks will appear when plans are created" />;
  }

  return (
    <div className="space-y-3">
      {tasks.map((t) => (
        <TaskCard key={t.task_id} task={t} />
      ))}
    </div>
  );
}

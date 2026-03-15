"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchTasks } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { TaskList } from "@/components/tasks/task-list";

export default function TasksPage() {
  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ["tasks"],
    queryFn: fetchTasks,
    refetchInterval: 30_000,
  });

  return (
    <div className="p-6">
      <PageHeader title="Tasks" subtitle="Plans and task graphs" />

      {isLoading ? (
        <p className="text-neutral-500 text-sm">Loading...</p>
      ) : (
        <TaskList tasks={tasks} />
      )}
    </div>
  );
}

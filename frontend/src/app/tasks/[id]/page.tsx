"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { fetchTask } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { TaskDetailView } from "@/components/tasks/task-detail";
import { Button } from "@/components/ui/button";
import Link from "next/link";

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data: task, isLoading, error } = useQuery({
    queryKey: ["task", id],
    queryFn: () => fetchTask(id),
    enabled: !!id,
  });

  return (
    <div className="p-6">
      <PageHeader
        title="Task Detail"
        subtitle={id}
        actions={
          <Link href="/tasks">
            <Button variant="ghost" size="sm">
              Back to Tasks
            </Button>
          </Link>
        }
      />

      {isLoading && <p className="text-neutral-500 text-sm">Loading...</p>}
      {error && (
        <p className="text-red-400 text-sm">
          Failed to load task: {error instanceof Error ? error.message : "Unknown error"}
        </p>
      )}
      {task && <TaskDetailView task={task} />}
    </div>
  );
}

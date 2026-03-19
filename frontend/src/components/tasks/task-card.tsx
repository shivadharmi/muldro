import Link from "next/link";
import type { Task } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";

export function TaskCard({ task }: { task: Task }) {
  return (
    <Link href={`/tasks/${task.task_id}`}>
      <Card className="hover:border-b-primary transition-colors cursor-pointer">
        <CardBody>
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <p className="text-sm font-medium">{task.goal}</p>
              <p className="text-xs text-t-tertiary mt-1">{task.decision}</p>
            </div>
            <div className="flex items-center gap-2 ml-3">
              <Badge variant={priorityVariant(task.priority)}>{task.priority}</Badge>
              <Badge variant={statusVariant(task.status)}>{task.status}</Badge>
            </div>
          </div>
          <div className="mt-2">
            <TimeAgo date={task.created_at} className="text-xs" />
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}

"use client";

import type { TaskDetail as TaskDetailType } from "@/lib/types";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant, riskVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";
import { StepList } from "./step-list";

export function TaskDetailView({ task }: { task: TaskDetailType }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">{task.goal}</h2>
            <div className="flex items-center gap-2">
              <Badge variant={priorityVariant(task.priority)}>{task.priority}</Badge>
              <Badge variant={statusVariant(task.status)}>{task.status}</Badge>
              <Badge variant={riskVariant(task.risk_level)}>{task.risk_level} risk</Badge>
            </div>
          </div>
        </CardHeader>
        <CardBody>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-xs mb-3">
            <div>
              <span className="text-t-tertiary">Decision</span>
              <p className="text-t-primary mt-0.5">{task.decision}</p>
            </div>
            <div>
              <span className="text-t-tertiary">Execution</span>
              <p className="text-t-primary mt-0.5">{task.execution_status || "N/A"}</p>
            </div>
            <div>
              <span className="text-t-tertiary">Created</span>
              <div className="mt-0.5">
                <TimeAgo date={task.created_at} />
              </div>
            </div>
          </div>
          {task.reasoning_summary && (
            <div className="bg-surface-2 rounded p-3 text-xs text-t-secondary">
              <p className="text-t-tertiary font-medium mb-1">Reasoning</p>
              <InlineMarkdown content={task.reasoning_summary} />
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <span className="text-sm font-medium">Steps ({task.steps.length})</span>
        </CardHeader>
        <CardBody>
          <StepList steps={task.steps} />
        </CardBody>
      </Card>
    </div>
  );
}

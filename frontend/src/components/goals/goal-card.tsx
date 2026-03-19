"use client";

import { useState } from "react";
import type { Goal, GoalCreateInput } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant, priorityVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";

export function GoalCard({
  goal,
  onUpdate,
  onDelete,
}: {
  goal: Goal;
  onUpdate: (id: string, input: Partial<GoalCreateInput & { status: string; progress: number }>) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(goal.title);
  const [editDescription, setEditDescription] = useState(goal.description || "");
  const [editPriority, setEditPriority] = useState(goal.priority);

  function handleSave() {
    onUpdate(goal.goal_id, {
      title: editTitle.trim(),
      description: editDescription.trim() || undefined,
      priority: editPriority,
    });
    setEditing(false);
  }

  function cycleStatus() {
    const nextStatus = goal.status === "active" ? "paused" : goal.status === "paused" ? "active" : goal.status;
    if (nextStatus !== goal.status) {
      onUpdate(goal.goal_id, { status: nextStatus });
    }
  }

  const progressPercent = Math.min(100, Math.max(0, goal.progress));

  if (editing) {
    return (
      <Card>
        <CardBody>
          <div className="space-y-3">
            <input
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              className="w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary"
            />
            <textarea
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              rows={2}
              className="w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary resize-none"
            />
            <div className="flex items-center gap-3">
              <select
                value={editPriority}
                onChange={(e) => setEditPriority(e.target.value)}
                className="bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
              <div className="flex-1" />
              <button
                onClick={() => setEditing(false)}
                className="text-t-secondary hover:text-t-primary text-xs px-3 py-1 rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                className="bg-j-primary hover:bg-j-primary-hover text-j-primary-fg text-xs px-3 py-1.5 rounded transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardBody>
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium">{goal.title}</p>
              <Badge variant={statusVariant(goal.status)}>{goal.status}</Badge>
              <Badge variant={priorityVariant(goal.priority)}>{goal.priority}</Badge>
            </div>
            {goal.description && (
              <p className="text-xs text-t-tertiary mt-1">{goal.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2 ml-3">
            <button
              onClick={cycleStatus}
              className="text-t-tertiary hover:text-t-primary text-xs transition-colors"
              title={goal.status === "active" ? "Pause" : "Resume"}
            >
              {goal.status === "active" ? "Pause" : goal.status === "paused" ? "Resume" : ""}
            </button>
            <button
              onClick={() => setEditing(true)}
              className="text-t-tertiary hover:text-t-primary text-xs transition-colors"
            >
              Edit
            </button>
            <button
              onClick={() => onDelete(goal.goal_id)}
              className="text-t-tertiary hover:text-j-error text-xs transition-colors"
            >
              Delete
            </button>
          </div>
        </div>

        <div className="mt-3">
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-surface-2 rounded-full h-2.5">
              <div
                className={`h-2.5 rounded-full transition-all ${
                  progressPercent > 80
                    ? "bg-j-success"
                    : progressPercent >= 40
                      ? "bg-j-primary"
                      : "bg-j-warning"
                }`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            <span className="text-xs text-t-tertiary shrink-0">{progressPercent}%</span>
          </div>
        </div>

        <div className="mt-2">
          <TimeAgo date={goal.created_at} className="text-xs" />
        </div>
      </CardBody>
    </Card>
  );
}

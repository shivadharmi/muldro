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
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
            />
            <textarea
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              rows={2}
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200 resize-none"
            />
            <div className="flex items-center gap-3">
              <select
                value={editPriority}
                onChange={(e) => setEditPriority(e.target.value)}
                className="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
              <div className="flex-1" />
              <button
                onClick={() => setEditing(false)}
                className="text-neutral-400 hover:text-neutral-200 text-xs px-3 py-1 rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                className="bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-1.5 rounded transition-colors"
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
              <p className="text-xs text-neutral-500 mt-1">{goal.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2 ml-3">
            <button
              onClick={cycleStatus}
              className="text-neutral-500 hover:text-neutral-300 text-xs transition-colors"
              title={goal.status === "active" ? "Pause" : "Resume"}
            >
              {goal.status === "active" ? "Pause" : goal.status === "paused" ? "Resume" : ""}
            </button>
            <button
              onClick={() => setEditing(true)}
              className="text-neutral-500 hover:text-neutral-300 text-xs transition-colors"
            >
              Edit
            </button>
            <button
              onClick={() => onDelete(goal.goal_id)}
              className="text-neutral-500 hover:text-red-400 text-xs transition-colors"
            >
              Delete
            </button>
          </div>
        </div>

        <div className="mt-3">
          <div className="flex items-center justify-between text-xs text-neutral-500 mb-1">
            <span>Progress</span>
            <span>{progressPercent}%</span>
          </div>
          <div className="w-full bg-neutral-800 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${
                progressPercent >= 100
                  ? "bg-green-500"
                  : progressPercent >= 50
                    ? "bg-blue-500"
                    : "bg-yellow-500"
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        <div className="mt-2">
          <TimeAgo date={goal.created_at} className="text-xs" />
        </div>
      </CardBody>
    </Card>
  );
}

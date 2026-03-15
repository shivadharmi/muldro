"use client";

import { useState } from "react";
import type { ScheduleCreateInput } from "@/lib/types";
import { Button } from "@/components/ui/button";

export function ScheduleForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (input: ScheduleCreateInput) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [scheduleType, setScheduleType] = useState("recurring");
  const [cronExpr, setCronExpr] = useState("");
  const [runAt, setRunAt] = useState("");
  const [actionType, setActionType] = useState("daily_briefing");
  const [actionConfig, setActionConfig] = useState("{}");
  const [priority, setPriority] = useState("medium");
  const [enabled, setEnabled] = useState(true);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    let parsedConfig: Record<string, unknown> = {};
    try {
      parsedConfig = JSON.parse(actionConfig);
    } catch {
      // ignore parse error
    }
    onSubmit({
      name,
      schedule_type: scheduleType,
      cron_expr: scheduleType === "recurring" ? cronExpr : undefined,
      run_at: scheduleType === "one_shot" ? runAt : undefined,
      action_type: actionType,
      action_config: parsedConfig,
      priority,
      enabled,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs text-neutral-500 mb-1">Name</label>
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-neutral-500 mb-1">Schedule Type</label>
          <select
            value={scheduleType}
            onChange={(e) => setScheduleType(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="recurring">Recurring</option>
            <option value="one_shot">One Shot</option>
          </select>
        </div>
        <div>
          {scheduleType === "recurring" ? (
            <>
              <label className="block text-xs text-neutral-500 mb-1">Cron Expression</label>
              <input
                value={cronExpr}
                onChange={(e) => setCronExpr(e.target.value)}
                placeholder="0 8 * * *"
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200 font-mono placeholder:text-neutral-600"
              />
            </>
          ) : (
            <>
              <label className="block text-xs text-neutral-500 mb-1">Run At (ISO)</label>
              <input
                type="datetime-local"
                value={runAt}
                onChange={(e) => setRunAt(e.target.value)}
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
              />
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-neutral-500 mb-1">Action Type</label>
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="daily_briefing">Daily Briefing</option>
            <option value="observe_sources">Observe Sources</option>
            <option value="heartbeat">Heartbeat</option>
            <option value="custom">Custom</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-neutral-500 mb-1">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>

      <div>
        <label className="block text-xs text-neutral-500 mb-1">Action Config (JSON)</label>
        <textarea
          value={actionConfig}
          onChange={(e) => setActionConfig(e.target.value)}
          rows={3}
          className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-neutral-200 font-mono"
        />
      </div>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="enabled"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded"
        />
        <label htmlFor="enabled" className="text-xs text-neutral-400">
          Enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm">
          Create Schedule
        </Button>
      </div>
    </form>
  );
}

"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ScheduleCreateInput } from "@/lib/types";
import { fetchScheduleSchema } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface ConfigField {
  name: string;
  type: string;
  label: string;
  required?: boolean;
  default?: unknown;
  placeholder?: string;
  options?: { value: string; label: string }[];
}

interface ActionTypeSchema {
  action_type: string;
  label: string;
  description: string;
  config_fields: ConfigField[];
  suggested_cron?: string;
}

export function ScheduleForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (input: ScheduleCreateInput) => void;
  onCancel: () => void;
}) {
  const { data: schema } = useQuery({
    queryKey: ["schedule-schema"],
    queryFn: fetchScheduleSchema,
    staleTime: 5 * 60_000,
  });

  const [name, setName] = useState("");
  const [scheduleType, setScheduleType] = useState("recurring");
  const [cronExpr, setCronExpr] = useState("");
  const [runAt, setRunAt] = useState("");
  const [actionType, setActionType] = useState("");
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [priority, setPriority] = useState("medium");
  const [enabled, setEnabled] = useState(true);

  const actionTypes = (schema?.action_types as ActionTypeSchema[]) ?? [];
  const cronPresets = (schema?.cron_presets as { value: string; label: string }[]) ?? [];

  const selectedAction = useMemo(
    () => actionTypes.find((a) => a.action_type === actionType),
    [actionTypes, actionType]
  );

  // When action type changes, reset config and suggest cron
  const handleActionTypeChange = (newType: string) => {
    setActionType(newType);
    setConfigValues({});
    const action = actionTypes.find((a) => a.action_type === newType);
    if (action?.suggested_cron && !cronExpr) {
      setCronExpr(action.suggested_cron);
    }
  };

  const handleConfigChange = (fieldName: string, value: unknown) => {
    setConfigValues((prev) => ({ ...prev, [fieldName]: value }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const config: Record<string, unknown> = {};
    for (const field of selectedAction?.config_fields ?? []) {
      const val = configValues[field.name];
      if (val !== undefined && val !== "") {
        config[field.name] = val;
      }
    }
    onSubmit({
      name,
      schedule_type: scheduleType,
      cron_expr: scheduleType === "recurring" ? cronExpr : undefined,
      run_at: scheduleType === "one_shot" ? runAt : undefined,
      action_type: actionType,
      action_config: config,
      priority,
      enabled,
    });
  };

  const inputClass =
    "w-full bg-surface-2 border border-b-primary rounded px-3 py-1.5 text-sm text-t-primary";

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs text-t-tertiary mb-1">Name</label>
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., Morning Briefing"
          className={inputClass}
        />
      </div>

      {/* Action Type — from schema */}
      <div>
        <label className="block text-xs text-t-tertiary mb-1">Action Type</label>
        <select
          required
          value={actionType}
          onChange={(e) => handleActionTypeChange(e.target.value)}
          className={inputClass}
        >
          <option value="">Select an action...</option>
          {actionTypes.map((a) => (
            <option key={a.action_type} value={a.action_type}>
              {a.label}
            </option>
          ))}
        </select>
        {selectedAction && (
          <p className="text-[10px] text-t-muted mt-1">{selectedAction.description}</p>
        )}
      </div>

      {/* Dynamic config fields */}
      {selectedAction?.config_fields.map((field) => (
        <div key={field.name}>
          <label className="block text-xs text-t-tertiary mb-1">
            {field.label}
            {field.required && <span className="text-j-error ml-0.5">*</span>}
          </label>
          {field.type === "select" && field.options ? (
            <select
              required={field.required}
              value={(configValues[field.name] as string) ?? (field.default as string) ?? ""}
              onChange={(e) => handleConfigChange(field.name, e.target.value)}
              className={inputClass}
            >
              <option value="">Select...</option>
              {field.options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          ) : field.type === "number" ? (
            <input
              type="number"
              required={field.required}
              value={(configValues[field.name] as number) ?? (field.default as number) ?? ""}
              onChange={(e) => handleConfigChange(field.name, Number(e.target.value))}
              placeholder={field.placeholder}
              className={inputClass}
            />
          ) : (
            <input
              type="text"
              required={field.required}
              value={(configValues[field.name] as string) ?? ""}
              onChange={(e) => handleConfigChange(field.name, e.target.value)}
              placeholder={field.placeholder}
              className={inputClass}
            />
          )}
        </div>
      ))}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-t-tertiary mb-1">Schedule Type</label>
          <select
            value={scheduleType}
            onChange={(e) => setScheduleType(e.target.value)}
            className={inputClass}
          >
            <option value="recurring">Recurring</option>
            <option value="one_shot">One Shot</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-t-tertiary mb-1">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className={inputClass}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>

      {/* Cron / Run At */}
      {scheduleType === "recurring" ? (
        <div>
          <label className="block text-xs text-t-tertiary mb-1">Cron Expression</label>
          <div className="flex gap-2">
            <input
              required
              value={cronExpr}
              onChange={(e) => setCronExpr(e.target.value)}
              placeholder="0 7 * * *"
              className={`${inputClass} font-mono flex-1`}
            />
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) setCronExpr(e.target.value);
              }}
              className="bg-surface-2 border border-b-primary rounded px-2 py-1.5 text-xs text-t-secondary"
            >
              <option value="">Presets...</option>
              {cronPresets.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <p className="text-[10px] text-t-muted mt-1">
            Format: minute hour day-of-month month day-of-week
          </p>
        </div>
      ) : (
        <div>
          <label className="block text-xs text-t-tertiary mb-1">Run At</label>
          <input
            type="datetime-local"
            required
            value={runAt}
            onChange={(e) => setRunAt(e.target.value)}
            className={inputClass}
          />
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="enabled"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded"
        />
        <label htmlFor="enabled" className="text-xs text-t-secondary">
          Enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!actionType || !name}>
          Create Schedule
        </Button>
      </div>
    </form>
  );
}

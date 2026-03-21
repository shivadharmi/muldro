"use client";

import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchTriggers,
  fetchTriggerSchema,
  createTrigger,
  deleteTrigger,
  toggleTrigger,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";

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
  value: string;
  label: string;
  description: string;
  config_fields: ConfigField[];
}

interface EventType {
  value: string;
  label: string;
  source: string;
}

export default function TriggersPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [showForm, setShowForm] = useState(false);

  // Form state
  const [name, setName] = useState("");
  const [eventType, setEventType] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [importance, setImportance] = useState("");
  const [entityMatch, setEntityMatch] = useState("");
  const [actionType, setActionType] = useState("notify");
  const [actionConfigValues, setActionConfigValues] = useState<Record<string, unknown>>({});

  const { data: schema } = useQuery({
    queryKey: ["trigger-schema"],
    queryFn: fetchTriggerSchema,
    staleTime: 5 * 60_000,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["triggers"],
    queryFn: fetchTriggers,
    refetchInterval: 30_000,
  });

  const triggers = data?.triggers || [];

  const eventTypes = (schema?.event_types as EventType[]) ?? [];
  const eventTypesBySource = (schema?.event_types_by_source as Record<string, EventType[]>) ?? {};
  const actionTypes = (schema?.action_types as ActionTypeSchema[]) ?? [];

  const sources = useMemo(() => Object.keys(eventTypesBySource).sort(), [eventTypesBySource]);

  const filteredEventTypes = useMemo(() => {
    if (!sourceFilter) return eventTypes;
    return eventTypes.filter((e) => e.source === sourceFilter);
  }, [eventTypes, sourceFilter]);

  const selectedActionSchema = useMemo(
    () => actionTypes.find((a) => a.value === actionType),
    [actionTypes, actionType]
  );

  const handleActionConfigChange = (fieldName: string, value: unknown) => {
    setActionConfigValues((prev) => ({ ...prev, [fieldName]: value }));
  };

  const resetForm = () => {
    setName("");
    setEventType("");
    setSourceFilter("");
    setImportance("");
    setEntityMatch("");
    setActionType("notify");
    setActionConfigValues({});
  };

  const createMut = useMutation({
    mutationFn: (input: Record<string, unknown>) => createTrigger(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
      setShowForm(false);
      resetForm();
      addToast("Trigger created", "success");
    },
    onError: (err) => addToast(`Failed to create trigger: ${err.message}`, "error"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteTrigger(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
      addToast("Trigger deleted", "success");
    },
    onError: (err) => addToast(`Failed to delete trigger: ${err.message}`, "error"),
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleTrigger(id, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
    },
    onError: (err) => addToast(`Failed to toggle trigger: ${err.message}`, "error"),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !eventType) return;

    const conditions: Record<string, unknown> = { event_type: eventType };
    if (sourceFilter) conditions.source = sourceFilter;
    if (importance) conditions.importance_threshold = importance;
    if (entityMatch.trim()) conditions.entity_match = entityMatch.trim();

    const actionConfig: Record<string, unknown> = {};
    for (const field of selectedActionSchema?.config_fields ?? []) {
      const val = actionConfigValues[field.name];
      if (val !== undefined && val !== "") {
        actionConfig[field.name] = val;
      }
    }

    createMut.mutate({
      name,
      conditions,
      action_type: actionType,
      action_config: actionConfig,
    });
  };

  const inputClass =
    "w-full rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-1 focus:ring-j-ring";

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex items-start justify-between">
        <PageHeader
          title="Triggers"
          subtitle="Reactive automation rules that fire on events"
        />
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-3 py-1.5 rounded-lg bg-j-primary text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover"
        >
          {showForm ? "Cancel" : "New Trigger"}
        </button>
      </div>

      {showForm && (
        <Card>
          <form onSubmit={handleCreate} className="p-4 space-y-3">
            {/* Name */}
            <div className="space-y-1">
              <label className="text-xs text-t-secondary">Name</label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Notify on PR reviews"
                className={inputClass}
              />
            </div>

            {/* Event Type with source filter */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-t-secondary">Source (optional filter)</label>
                <select
                  value={sourceFilter}
                  onChange={(e) => {
                    setSourceFilter(e.target.value);
                    setEventType("");
                  }}
                  className={inputClass}
                >
                  <option value="">All Sources</option>
                  {sources.map((s) => (
                    <option key={s} value={s}>
                      {s.charAt(0).toUpperCase() + s.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-t-secondary">
                  Event Type <span className="text-j-error">*</span>
                </label>
                <select
                  required
                  value={eventType}
                  onChange={(e) => setEventType(e.target.value)}
                  className={inputClass}
                >
                  <option value="">Select event...</option>
                  {filteredEventTypes.map((evt) => (
                    <option key={evt.value} value={evt.value}>
                      {evt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Additional conditions */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-t-secondary">Minimum Importance</label>
                <select
                  value={importance}
                  onChange={(e) => setImportance(e.target.value)}
                  className={inputClass}
                >
                  <option value="">Any</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-t-secondary">Entity Match</label>
                <input
                  type="text"
                  value={entityMatch}
                  onChange={(e) => setEntityMatch(e.target.value)}
                  placeholder="e.g., john@example.com"
                  className={inputClass}
                />
              </div>
            </div>

            {/* Action Type */}
            <div className="space-y-1">
              <label className="text-xs text-t-secondary">Action</label>
              <select
                value={actionType}
                onChange={(e) => {
                  setActionType(e.target.value);
                  setActionConfigValues({});
                }}
                className={inputClass}
              >
                {actionTypes.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
              {selectedActionSchema && (
                <p className="text-[10px] text-t-muted">{selectedActionSchema.description}</p>
              )}
            </div>

            {/* Dynamic action config fields */}
            {selectedActionSchema?.config_fields.map((field) => (
              <div key={field.name} className="space-y-1">
                <label className="text-xs text-t-secondary">
                  {field.label}
                  {field.required && <span className="text-j-error ml-0.5">*</span>}
                </label>
                {field.type === "select" && field.options ? (
                  <select
                    required={field.required}
                    value={
                      (actionConfigValues[field.name] as string) ??
                      (field.default as string) ??
                      ""
                    }
                    onChange={(e) => handleActionConfigChange(field.name, e.target.value)}
                    className={inputClass}
                  >
                    <option value="">Select...</option>
                    {field.options.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                ) : field.type === "json" ? (
                  <textarea
                    value={(actionConfigValues[field.name] as string) ?? ""}
                    onChange={(e) => handleActionConfigChange(field.name, e.target.value)}
                    placeholder={field.placeholder}
                    rows={2}
                    className={`${inputClass} font-mono`}
                  />
                ) : (
                  <input
                    type="text"
                    required={field.required}
                    value={(actionConfigValues[field.name] as string) ?? ""}
                    onChange={(e) => handleActionConfigChange(field.name, e.target.value)}
                    placeholder={field.placeholder}
                    className={inputClass}
                  />
                )}
              </div>
            ))}

            <button
              type="submit"
              disabled={createMut.isPending || !name || !eventType}
              className="px-4 py-2 rounded-lg bg-j-primary text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
            >
              {createMut.isPending ? "Creating..." : "Create Trigger"}
            </button>
          </form>
        </Card>
      )}

      {isLoading && (
        <div className="text-center py-12 text-t-tertiary text-sm">Loading...</div>
      )}

      {!isLoading && triggers.length === 0 && !showForm && (
        <div className="text-center py-12 text-t-tertiary text-sm">
          No triggers configured yet
        </div>
      )}

      <div className="space-y-3">
        {triggers.map((trigger: Record<string, unknown>) => {
          const id = trigger.trigger_id as string;
          const enabled = trigger.enabled !== false;
          const conditions = trigger.conditions as Record<string, unknown> | undefined;

          return (
            <Card key={id}>
              <div className="p-4 flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-medium text-t-primary">
                      {(trigger.name as string) || "Untitled"}
                    </p>
                    <Badge variant={enabled ? "green" : "default"}>
                      {enabled ? "Active" : "Disabled"}
                    </Badge>
                    <Badge variant="blue">
                      {(trigger.action_type as string) || "notify"}
                    </Badge>
                  </div>
                  {conditions && (
                    <p className="text-xs text-t-tertiary">
                      When: {conditions.event_type as string || "any"}
                      {conditions.source ? ` from ${conditions.source}` : ""}
                      {conditions.importance_threshold
                        ? ` (≥${conditions.importance_threshold})`
                        : ""}
                    </p>
                  )}
                  {trigger.description ? (
                    <p className="text-[10px] text-t-muted mt-0.5">
                      {String(trigger.description)}
                    </p>
                  ) : null}
                  {trigger.fire_count !== undefined && (
                    <p className="text-[10px] text-t-muted mt-1">
                      Fired {trigger.fire_count as number} times
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() =>
                      toggleMut.mutate({ id, enabled: !enabled })
                    }
                    className="text-xs text-t-secondary hover:text-t-primary"
                  >
                    {enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm("Delete this trigger?")) {
                        deleteMut.mutate(id);
                      }
                    }}
                    className="text-xs text-j-error hover:text-j-error"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

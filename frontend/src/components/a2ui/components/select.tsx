"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";
import { FOCUS_RING } from "@/lib/focus-ring";

interface Props {
  component: A2UIComponent;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

export function A2UISelect({ component, onAction }: Props) {
  const label = (component.properties.label as string) || "";
  const options = (component.properties.options as Array<{ value: string; label: string }>) || [];
  const [value, setValue] = useState((component.properties.value as string) || "");

  const handleChange = (newValue: string) => {
    setValue(newValue);
    if (component.actions.length > 0) {
      const action = component.actions[0];
      onAction(action.type, { ...action.payload, value: newValue });
    }
  };

  return (
    <div className="space-y-1">
      {label && <label className="text-xs text-t-secondary">{label}</label>}
      <select
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        className={`w-full rounded-[var(--radius-sm)] bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary ${FOCUS_RING}`}
      >
        <option value="">Select...</option>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </div>
  );
}

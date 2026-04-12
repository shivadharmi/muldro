"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";
import { FOCUS_RING } from "@/lib/focus-ring";

interface Props {
  component: A2UIComponent;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

export function A2UITextField({ component, onAction }: Props) {
  const placeholder = (component.properties.placeholder as string) || "";
  const label = (component.properties.label as string) || "";
  const [value, setValue] = useState("");

  const handleSubmit = () => {
    if (component.actions.length > 0 && value.trim()) {
      const action = component.actions[0];
      onAction(action.type, { ...action.payload, value });
      setValue("");
    }
  };

  return (
    <div className="space-y-1">
      {label && <label className="text-xs text-t-secondary">{label}</label>}
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
        placeholder={placeholder}
        className={`w-full rounded-[var(--radius-sm)] bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary placeholder-t-tertiary ${FOCUS_RING}`}
      />
    </div>
  );
}

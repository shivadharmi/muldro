"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";
import { FOCUS_RING } from "@/lib/focus-ring";

interface Props {
  component: A2UIComponent;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

export function A2UIToggle({ component, onAction }: Props) {
  const label = (component.properties.label as string) || "";
  const [checked, setChecked] = useState(component.properties.checked as boolean || false);

  const handleToggle = () => {
    const newVal = !checked;
    setChecked(newVal);
    if (component.actions.length > 0) {
      const action = component.actions[0];
      onAction(action.type, { ...action.payload, checked: newVal });
    }
  };

  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={handleToggle}
        className={`relative w-9 h-5 rounded-full transition-colors ${FOCUS_RING} ${checked ? "bg-j-primary" : "bg-b-primary"}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : ""}`}
        />
      </button>
      <span className="text-sm text-t-primary">{label}</span>
    </label>
  );
}

"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";

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
        className={`relative w-9 h-5 rounded-full transition-colors ${checked ? "bg-blue-600" : "bg-neutral-700"}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : ""}`}
        />
      </button>
      <span className="text-sm text-neutral-300">{label}</span>
    </label>
  );
}

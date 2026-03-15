"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
  renderChild: (child: A2UIComponent) => ReactNode;
}

export function A2UITabs({ component, renderChild }: Props) {
  const labels = (component.properties.labels as string[]) || [];
  const [active, setActive] = useState((component.properties.active_tab as number) || 0);
  const tabChildren = component.children || [];

  return (
    <div>
      <div className="flex border-b border-neutral-800 mb-3">
        {labels.map((label, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={`px-3 py-2 text-sm font-medium transition-colors ${
              active === i
                ? "text-blue-400 border-b-2 border-blue-400"
                : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tabChildren[active] && renderChild(tabChildren[active])}
    </div>
  );
}

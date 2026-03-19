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
      <div className="flex border-b border-b-primary mb-3">
        {labels.map((label, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={`px-3 py-2 text-sm font-medium transition-colors ${
              active === i
                ? "text-j-primary border-b-2 border-j-primary"
                : "text-t-tertiary hover:text-t-primary"
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

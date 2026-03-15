import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UIForm({ children }: Props) {
  return (
    <div className="space-y-3 rounded-lg border border-neutral-800 bg-neutral-900 p-4">
      {children}
    </div>
  );
}

import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UICard({ children }: Props) {
  return (
    <div className="rounded-[var(--radius-lg)] border border-b-primary bg-surface-1 p-4 space-y-2">
      {children}
    </div>
  );
}

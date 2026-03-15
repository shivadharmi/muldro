import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UICard({ children }: Props) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-4 space-y-2">
      {children}
    </div>
  );
}

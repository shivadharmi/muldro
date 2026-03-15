import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UIList({ children }: Props) {
  return <div className="space-y-2">{children}</div>;
}

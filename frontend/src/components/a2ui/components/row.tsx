import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UIRow({ children }: Props) {
  return <div className="flex items-center gap-2">{children}</div>;
}

import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIDivider({}: Props) {
  return <hr className="my-3 border-b-primary" />;
}

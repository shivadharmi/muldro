import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const variantClasses: Record<string, string> = {
  default: "bg-neutral-700 text-neutral-300",
  primary: "bg-blue-900/50 text-blue-300",
  success: "bg-green-900/50 text-green-300",
  warning: "bg-yellow-900/50 text-yellow-300",
  danger: "bg-red-900/50 text-red-300",
  low: "bg-green-900/50 text-green-300",
  medium: "bg-yellow-900/50 text-yellow-300",
  high: "bg-red-900/50 text-red-300",
  running: "bg-blue-900/50 text-blue-300",
  completed: "bg-green-900/50 text-green-300",
  failed: "bg-red-900/50 text-red-300",
  pending: "bg-yellow-900/50 text-yellow-300",
};

export function A2UIBadge({ component }: Props) {
  const label = (component.properties.label as string) || "";
  const variant = (component.properties.variant as string) || "default";
  const cls = variantClasses[variant] || variantClasses.default;

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}

import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const variantClasses: Record<string, string> = {
  default: "bg-surface-3 text-t-primary",
  primary: "bg-j-primary-soft text-j-primary",
  success: "bg-j-success-soft text-j-success",
  warning: "bg-j-warning-soft text-j-warning",
  danger: "bg-j-error-soft text-j-error",
  low: "bg-j-success-soft text-j-success",
  medium: "bg-j-warning-soft text-j-warning",
  high: "bg-j-error-soft text-j-error",
  running: "bg-j-primary-soft text-j-primary",
  completed: "bg-j-success-soft text-j-success",
  failed: "bg-j-error-soft text-j-error",
  pending: "bg-j-warning-soft text-j-warning",
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

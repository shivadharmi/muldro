import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const severityClasses: Record<string, string> = {
  info: "border-j-info/30 bg-j-info-soft text-j-info",
  success: "border-j-success/30 bg-j-success-soft text-j-success",
  warning: "border-j-warning/30 bg-j-warning-soft text-j-warning",
  error: "border-j-error/30 bg-j-error-soft text-j-error",
};

export function A2UIAlert({ component }: Props) {
  const message = (component.properties.message as string) || "";
  const severity = (component.properties.severity as string) || "info";
  const title = component.properties.title as string | undefined;
  const cls = severityClasses[severity] || severityClasses.info;

  return (
    <div className={`rounded-[var(--radius-lg)] border p-3 ${cls}`}>
      {title && <p className="text-sm font-medium mb-1">{title}</p>}
      <p className="text-sm">{message}</p>
    </div>
  );
}

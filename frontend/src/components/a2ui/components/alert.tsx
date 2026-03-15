import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const severityClasses: Record<string, string> = {
  info: "border-blue-800 bg-blue-950/40 text-blue-300",
  success: "border-green-800 bg-green-950/40 text-green-300",
  warning: "border-yellow-800 bg-yellow-950/40 text-yellow-300",
  error: "border-red-800 bg-red-950/40 text-red-300",
};

export function A2UIAlert({ component }: Props) {
  const message = (component.properties.message as string) || "";
  const severity = (component.properties.severity as string) || "info";
  const title = component.properties.title as string | undefined;
  const cls = severityClasses[severity] || severityClasses.info;

  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      {title && <p className="text-sm font-medium mb-1">{title}</p>}
      <p className="text-sm">{message}</p>
    </div>
  );
}

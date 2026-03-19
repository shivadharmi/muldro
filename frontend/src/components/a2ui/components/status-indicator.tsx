import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const statusColors: Record<string, string> = {
  healthy: "bg-j-success",
  ok: "bg-j-success",
  connected: "bg-j-success",
  degraded: "bg-j-warning",
  warning: "bg-j-warning",
  error: "bg-j-error",
  disconnected: "bg-j-error",
  unknown: "bg-t-tertiary",
};

export function A2UIStatusIndicator({ component }: Props) {
  const status = (component.properties.status as string) || "unknown";
  const label = (component.properties.label as string) || status;
  const color = statusColors[status] || statusColors.unknown;

  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-xs text-t-primary">{label}</span>
    </div>
  );
}

import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const statusColors: Record<string, string> = {
  healthy: "bg-green-500",
  ok: "bg-green-500",
  connected: "bg-green-500",
  degraded: "bg-yellow-500",
  warning: "bg-yellow-500",
  error: "bg-red-500",
  disconnected: "bg-red-500",
  unknown: "bg-neutral-500",
};

export function A2UIStatusIndicator({ component }: Props) {
  const status = (component.properties.status as string) || "unknown";
  const label = (component.properties.label as string) || status;
  const color = statusColors[status] || statusColors.unknown;

  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-xs text-neutral-300">{label}</span>
    </div>
  );
}

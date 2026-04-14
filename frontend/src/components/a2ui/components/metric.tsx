import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIMetric({ component }: Props) {
  const label = (component.properties.label as string) || "";
  const value = component.properties.value;
  const change = component.properties.change as string | undefined;
  const trend = component.properties.trend as string | undefined;

  const trendColor = trend === "up" ? "text-j-success" : trend === "down" ? "text-j-error" : "text-t-tertiary";

  return (
    <div className="rounded-[var(--radius-lg)] border border-b-primary bg-surface-1 p-3 min-w-[120px]">
      <p className="text-xs text-t-tertiary mb-1">{label}</p>
      <p className="text-xl font-semibold text-t-primary">{String(value)}</p>
      {change && (
        <p className={`text-xs mt-1 ${trendColor}`}>{change}</p>
      )}
    </div>
  );
}

import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIMetric({ component }: Props) {
  const label = (component.properties.label as string) || "";
  const value = component.properties.value;
  const change = component.properties.change as string | undefined;
  const trend = component.properties.trend as string | undefined;

  const trendColor = trend === "up" ? "text-green-400" : trend === "down" ? "text-red-400" : "text-neutral-500";

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-3 min-w-[120px]">
      <p className="text-xs text-neutral-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-white">{String(value)}</p>
      {change && (
        <p className={`text-xs mt-1 ${trendColor}`}>{change}</p>
      )}
    </div>
  );
}

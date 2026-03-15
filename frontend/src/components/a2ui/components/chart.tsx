import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIChart({ component }: Props) {
  const chartType = (component.properties.chart_type as string) || "bar";
  const title = (component.properties.title as string) || "";
  const data = component.properties.data as Record<string, unknown> | undefined;
  const values = (data?.values as number[]) || [];
  const labels = (data?.labels as string[]) || [];
  const maxVal = Math.max(...values, 1);

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
      {title && <p className="text-sm font-medium text-white mb-3">{title}</p>}
      {chartType === "bar" && (
        <div className="flex items-end gap-1 h-32">
          {values.map((v, i) => (
            <div key={i} className="flex-1 flex flex-col items-center gap-1">
              <div
                className="w-full bg-blue-500/70 rounded-t transition-all"
                style={{ height: `${(v / maxVal) * 100}%` }}
              />
              <span className="text-[10px] text-neutral-500 truncate max-w-full">
                {labels[i] || ""}
              </span>
            </div>
          ))}
        </div>
      )}
      {chartType !== "bar" && (
        <p className="text-xs text-neutral-500">Chart type: {chartType}</p>
      )}
    </div>
  );
}

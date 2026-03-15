import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIProgress({ component }: Props) {
  const value = (component.properties.value as number) || 0;
  const max = (component.properties.max as number) || 100;
  const label = component.properties.label as string | undefined;
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;

  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between text-xs text-neutral-400">
          <span>{label}</span>
          <span>{Math.round(pct)}%</span>
        </div>
      )}
      <div className="h-2 rounded-full bg-neutral-800 overflow-hidden">
        <div
          className="h-full rounded-full bg-blue-500 transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const typeLabels: Record<string, string> = {
  episodic: "Episodic",
  semantic: "Semantic",
  preference: "Preference",
  procedural: "Procedural",
};

export function A2UIMemoryCard({ component }: Props) {
  const factText = (component.properties.fact_text as string) || "";
  const memoryType = (component.properties.memory_type as string) || "";
  const source = (component.properties.source as string) || "";
  const confidence = (component.properties.confidence as number) ?? 1.0;
  const pct = Math.round(confidence * 100);

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-3">
      <div className="flex items-start justify-between mb-1">
        <span className="text-[10px] font-medium text-blue-400 uppercase">
          {typeLabels[memoryType] || memoryType}
        </span>
        <span className="text-[10px] text-neutral-500">{pct}%</span>
      </div>
      <p className="text-sm text-neutral-300">{factText}</p>
      {source && (
        <p className="text-[10px] text-neutral-600 mt-1 font-mono">{source}</p>
      )}
      <div className="mt-2 h-1 rounded-full bg-neutral-800 overflow-hidden">
        <div
          className="h-full rounded-full bg-blue-500/50"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

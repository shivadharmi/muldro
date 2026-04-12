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
    <div className="rounded-[var(--radius-lg)] border border-b-primary bg-surface-1 p-3">
      <div className="flex items-start justify-between mb-1">
        <span className="text-[10px] font-medium text-j-primary uppercase">
          {typeLabels[memoryType] || memoryType}
        </span>
        <span className="text-[10px] text-t-tertiary">{pct}%</span>
      </div>
      <p className="text-sm text-t-primary">{factText}</p>
      {source && (
        <p className="text-[10px] text-t-muted mt-1 font-mono">{source}</p>
      )}
      <div className="mt-2 h-1 rounded-full bg-surface-2 overflow-hidden">
        <div
          className="h-full rounded-full bg-j-primary/50"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

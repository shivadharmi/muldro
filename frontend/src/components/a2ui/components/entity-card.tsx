import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const typeColors: Record<string, string> = {
  person: "border-j-primary/30 bg-j-primary-soft",
  organization: "border-j-secondary/30 bg-j-secondary-soft",
  project: "border-j-success/30 bg-j-success-soft",
  meeting: "border-j-warning/30 bg-j-warning-soft",
  goal: "border-j-accent/30 bg-j-accent-soft",
};

export function A2UIEntityCard({ component }: Props) {
  const name = (component.properties.name as string) || "";
  const entityType = (component.properties.entity_type as string) || "";
  const entityId = (component.properties.entity_id as string) || "";
  const attributes = component.properties.attributes as Record<string, unknown> | undefined;
  const cls = typeColors[entityType] || "border-b-primary bg-surface-1";

  return (
    <div className={`rounded-[var(--radius-lg)] border p-3 ${cls}`}>
      <div className="flex items-start justify-between mb-1">
        <p className="text-sm font-medium text-t-primary">{name}</p>
        <span className="text-[10px] text-t-tertiary uppercase">{entityType}</span>
      </div>
      {entityId && <p className="text-[10px] text-t-muted font-mono mb-2">{entityId}</p>}
      {attributes && Object.keys(attributes).length > 0 && (
        <div className="space-y-0.5">
          {Object.entries(attributes).slice(0, 4).map(([k, v]) => (
            <div key={k} className="text-xs text-t-secondary">
              <span className="text-t-tertiary">{k}:</span> {String(v)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

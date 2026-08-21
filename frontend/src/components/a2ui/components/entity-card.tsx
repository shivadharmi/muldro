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

// Attributes travel as `[{ key, value }]`, positionally ordered. The legacy keyed object is
// still accepted so a surface persisted before this change still renders.
function attributePairs(attributes: unknown): Array<{ key: string; value: string }> {
  if (Array.isArray(attributes)) {
    return attributes.map((a) => {
      const pair = a as { key?: unknown; value?: unknown } | null;
      return { key: String(pair?.key ?? ""), value: String(pair?.value ?? "") };
    });
  }
  if (attributes && typeof attributes === "object") {
    return Object.entries(attributes as Record<string, unknown>).map(([key, value]) => ({
      key,
      value: String(value),
    }));
  }
  return [];
}

export function A2UIEntityCard({ component }: Props) {
  const name = (component.properties.name as string) || "";
  const entityType = (component.properties.entity_type as string) || "";
  const entityId = (component.properties.entity_id as string) || "";
  const attributes = attributePairs(component.properties.attributes);
  const cls = typeColors[entityType] || "border-b-primary bg-surface-1";

  return (
    <div className={`rounded-[var(--radius-lg)] border p-3 ${cls}`}>
      <div className="flex items-start justify-between mb-1">
        <p className="text-sm font-medium text-t-primary">{name}</p>
        <span className="text-[10px] text-t-tertiary uppercase">{entityType}</span>
      </div>
      {entityId && <p className="text-[10px] text-t-muted font-mono mb-2">{entityId}</p>}
      {attributes.length > 0 && (
        <div className="space-y-0.5">
          {attributes.slice(0, 4).map((attr, i) => (
            <div key={`${attr.key}-${i}`} className="text-xs text-t-secondary">
              <span className="text-t-tertiary">{attr.key}:</span> {attr.value}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

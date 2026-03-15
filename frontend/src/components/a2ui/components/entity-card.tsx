import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const typeColors: Record<string, string> = {
  person: "border-blue-800 bg-blue-950/20",
  organization: "border-purple-800 bg-purple-950/20",
  project: "border-green-800 bg-green-950/20",
  meeting: "border-yellow-800 bg-yellow-950/20",
  goal: "border-orange-800 bg-orange-950/20",
};

export function A2UIEntityCard({ component }: Props) {
  const name = (component.properties.name as string) || "";
  const entityType = (component.properties.entity_type as string) || "";
  const entityId = (component.properties.entity_id as string) || "";
  const attributes = component.properties.attributes as Record<string, unknown> | undefined;
  const cls = typeColors[entityType] || "border-neutral-800 bg-neutral-900";

  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <div className="flex items-start justify-between mb-1">
        <p className="text-sm font-medium text-white">{name}</p>
        <span className="text-[10px] text-neutral-500 uppercase">{entityType}</span>
      </div>
      {entityId && <p className="text-[10px] text-neutral-600 font-mono mb-2">{entityId}</p>}
      {attributes && Object.keys(attributes).length > 0 && (
        <div className="space-y-0.5">
          {Object.entries(attributes).slice(0, 4).map(([k, v]) => (
            <div key={k} className="text-xs text-neutral-400">
              <span className="text-neutral-500">{k}:</span> {String(v)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

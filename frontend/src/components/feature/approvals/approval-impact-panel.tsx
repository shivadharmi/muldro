"use client";

interface ImpactData {
  impact_summary: string;
  affected_entities: Array<{ entity_id: string; name: string; entity_type: string }>;
  reversibility: "easy" | "moderate" | "difficult" | "irreversible";
  blast_radius: "narrow" | "moderate" | "wide";
  downstream_effects: string[];
}

interface Props {
  impact: ImpactData | null;
}

const reversibilityColors: Record<string, string> = {
  easy: "text-status-success",
  moderate: "text-status-warning",
  difficult: "text-status-error",
  irreversible: "text-status-error font-medium",
};

const blastColors: Record<string, string> = {
  narrow: "bg-status-success/10 text-status-success",
  moderate: "bg-status-warning/10 text-status-warning",
  wide: "bg-status-error/10 text-status-error",
};

export function ApprovalImpactPanel({ impact }: Props) {
  if (!impact) {
    return (
      <div className="text-sm text-t-tertiary p-3">
        No impact analysis available.
      </div>
    );
  }

  return (
    <div className="space-y-3 p-3">
      <div>
        <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-1">
          Impact Summary
        </h4>
        <p className="text-sm text-t-primary">{impact.impact_summary}</p>
      </div>

      <div className="flex items-center gap-3">
        <div>
          <span className="text-xs text-t-tertiary">Reversibility: </span>
          <span className={`text-xs capitalize ${reversibilityColors[impact.reversibility] ?? "text-t-secondary"}`}>
            {impact.reversibility}
          </span>
        </div>
        <span className={`px-2 py-0.5 rounded text-xs ${blastColors[impact.blast_radius] ?? "bg-surface-1 text-t-tertiary"}`}>
          {impact.blast_radius} blast radius
        </span>
      </div>

      {impact.affected_entities.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-1">
            Affected Entities
          </h4>
          <ul className="space-y-1">
            {impact.affected_entities.map((e) => (
              <li key={e.entity_id} className="flex items-center gap-2 text-sm text-t-secondary">
                <span className="w-5 h-5 rounded bg-surface-2 flex items-center justify-center text-xs text-t-tertiary">
                  {e.entity_type[0]?.toUpperCase()}
                </span>
                <span>{e.name}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {impact.downstream_effects.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-1">
            Downstream Effects
          </h4>
          <ul className="space-y-1">
            {impact.downstream_effects.map((effect, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-t-secondary">
                <span className="text-status-warning mt-0.5">•</span>
                <span>{effect}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

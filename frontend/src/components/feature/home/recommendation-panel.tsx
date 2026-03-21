"use client";

import { RecommendationCard } from "@/components/primitives/recommendation-card";

interface RecommendedAction {
  action_type: string;
  title: string;
  description: string;
  priority?: string;
  action_url?: string;
  confidence?: number;
  evidence_count?: number;
}

interface Props {
  actions: RecommendedAction[];
  onAct?: (action: RecommendedAction) => void;
  onDismiss?: (action: RecommendedAction) => void;
}

export function RecommendationPanel({ actions, onAct, onDismiss }: Props) {
  if (actions.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
        Recommended Actions
      </h3>
      <div className="space-y-2">
        {actions.map((action, i) => (
          <RecommendationCard
            key={i}
            actionType={action.action_type}
            title={action.title}
            description={action.description}
            confidence={action.confidence}
            priority={action.priority}
            evidenceCount={action.evidence_count}
            onAct={onAct ? () => onAct(action) : undefined}
            onDismiss={onDismiss ? () => onDismiss(action) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

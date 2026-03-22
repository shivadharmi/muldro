"use client";

import { RecommendationCard } from "@/components/primitives/recommendation-card";

interface RecommendedAction {
  action_type: string;
  title: string;
  description: string;
  reasoning?: string;
  impact?: string;
  confidence?: number;
  priority?: string;
  priority_score?: number;
  action_url?: string;
}

interface Props {
  actions: RecommendedAction[];
  onDismiss?: (action: RecommendedAction) => void;
}

export function RecommendationPanel({ actions, onDismiss }: Props) {
  if (actions.length === 0) {
    return (
      <div className="space-y-2">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Recommended Actions
        </h3>
        <div className="py-4 text-center">
          <p className="text-sm text-t-tertiary">No actions needed right now.</p>
          <p className="text-xs text-t-tertiary mt-1">Jarvis is monitoring your workspace.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
          Recommended Actions
        </h3>
        <span className="text-[10px] text-t-tertiary">{actions.length} action{actions.length > 1 ? "s" : ""}</span>
      </div>
      <div className="space-y-2">
        {actions.map((action, i) => (
          <RecommendationCard
            key={`${action.action_type}-${i}`}
            actionType={action.action_type}
            title={action.title}
            description={action.description}
            reasoning={action.reasoning}
            impact={action.impact}
            confidence={action.confidence}
            priority={action.priority}
            actionUrl={action.action_url}
            onDismiss={onDismiss ? () => onDismiss(action) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

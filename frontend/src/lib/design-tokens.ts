/** Maps execution/task status to a Tailwind bg class */
export function statusColor(status: string): string {
  switch (status) {
    case "running":
    case "executing":
    case "in_progress":
      return "bg-j-info";
    case "completed":
    case "ok":
    case "approved":
    case "normal":
    case "healthy":
      return "bg-j-success";
    case "failed":
    case "rejected":
    case "error":
      return "bg-j-error";
    case "awaiting_approval":
    case "pending_approval":
    case "degraded":
      return "bg-j-warning";
    case "proposal":
      return "bg-j-secondary";
    case "pending":
    case "cancelled":
    case "paused":
    default:
      return "bg-t-muted";
  }
}

/** Maps execution/task status to a Tailwind text class */
export function statusTextColor(status: string): string {
  switch (status) {
    case "running":
    case "executing":
    case "in_progress":
      return "text-j-info";
    case "completed":
    case "ok":
    case "approved":
    case "normal":
    case "healthy":
      return "text-j-success";
    case "failed":
    case "rejected":
    case "error":
      return "text-j-error";
    case "awaiting_approval":
    case "pending_approval":
    case "degraded":
      return "text-j-warning";
    case "proposal":
    case "user_action":
      return "text-j-secondary";
    case "pending":
    case "cancelled":
    case "paused":
    default:
      return "text-t-muted";
  }
}

/** Maps execution phase to a Tailwind text class + optional pulse */
export function phaseTextColor(phase: string): { className: string; pulse: boolean } {
  switch (phase) {
    case "planning":
      return { className: "text-j-info", pulse: true };
    case "plan_ready":
      return { className: "text-j-info", pulse: false };
    case "executing":
      return { className: "text-j-info", pulse: true };
    case "approval_needed":
      return { className: "text-j-warning", pulse: true };
    case "completed":
      return { className: "text-j-success", pulse: false };
    case "failed":
      return { className: "text-j-error", pulse: false };
    case "partial":
      return { className: "text-j-warning", pulse: false };
    case "proposal":
      return { className: "text-j-secondary", pulse: true };
    default:
      return { className: "text-t-muted", pulse: false };
  }
}

/** Maps execution phase to a Tailwind bg class + optional pulse */
export function phaseBgColor(phase: string): { className: string; pulse: boolean } {
  switch (phase) {
    case "planning":
    case "executing":
      return { className: "bg-j-info", pulse: true };
    case "plan_ready":
      return { className: "bg-j-info", pulse: false };
    case "approval_needed":
      return { className: "bg-j-warning", pulse: true };
    case "completed":
      return { className: "bg-j-success", pulse: false };
    case "failed":
      return { className: "bg-j-error", pulse: false };
    case "partial":
      return { className: "bg-j-warning", pulse: false };
    case "proposal":
      return { className: "bg-j-secondary", pulse: true };
    default:
      return { className: "bg-t-muted", pulse: false };
  }
}

/** Maps risk level to a Tailwind bg class */
export function riskColor(risk: string): string {
  switch (risk) {
    case "low":
      return "bg-j-success";
    case "medium":
      return "bg-j-warning";
    case "high":
    case "critical":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps trust level to a Tailwind bg class */
export function trustLevelColor(level: string): string {
  switch (level) {
    case "first_use":
      return "bg-t-muted";
    case "learning":
      return "bg-j-info";
    case "trusted":
      return "bg-j-success";
    case "autonomous":
      return "bg-j-secondary";
    case "blocked":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps surface kind to badge styling (bg + text classes) */
export function kindStyle(kind: string): { bg: string; text: string } {
  switch (kind) {
    case "plan":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "approval":
      return { bg: "bg-j-warning-soft", text: "text-j-warning" };
    case "briefing":
      return { bg: "bg-j-success-soft", text: "text-j-success" };
    case "alert":
      return { bg: "bg-j-error-soft", text: "text-j-error" };
    case "proactive_insight":
    case "recommendation":
      return { bg: "bg-j-secondary-soft", text: "text-j-secondary" };
    case "execution":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    default:
      return { bg: "bg-surface-3", text: "text-t-secondary" };
  }
}

/** Maps priority to badge styling (bg + text classes) */
export function priorityStyle(priority: string): { bg: string; text: string } {
  switch (priority) {
    case "low":
      return { bg: "bg-surface-3", text: "text-t-secondary" };
    case "medium":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "high":
      return { bg: "bg-j-warning-soft", text: "text-j-warning" };
    case "critical":
      return { bg: "bg-j-error-soft", text: "text-j-error" };
    default:
      return { bg: "bg-surface-3", text: "text-t-secondary" };
  }
}

/** Maps search source DB to badge styling */
export function sourceDbStyle(db: string): string {
  switch (db) {
    case "qdrant":
      return "bg-j-info-soft text-j-info";
    case "postgres_fts":
      return "bg-j-success-soft text-j-success";
    case "neo4j":
      return "bg-j-secondary-soft text-j-secondary";
    default:
      return "bg-surface-2 text-t-tertiary";
  }
}

/** Human-readable labels for trust levels */
export const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

/** Human-readable labels for surface kinds */
export const KIND_LABELS: Record<string, string> = {
  plan: "Plan",
  approval: "Approval",
  briefing: "Briefing",
  alert: "Alert",
  summary: "Summary",
  recommendation: "Rec",
  proactive_insight: "Insight",
  execution: "Execution",
  checklist: "Checklist",
  comparison: "Compare",
  timeline: "Timeline",
  table: "Table",
  activity: "Activity",
};

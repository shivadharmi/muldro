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

/** Maps risk level to a Tailwind bg class */
export function riskLevelColor(level: string): string {
  switch (level) {
    case "none":
      return "bg-t-muted";
    case "low":
      return "bg-j-info";
    case "medium":
      return "bg-j-warning";
    case "high":
      return "bg-j-error";
    case "critical":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps risk level to a Tailwind text class */
export function riskLevelTextColor(level: string): string {
  switch (level) {
    case "none":
      return "text-t-muted";
    case "low":
      return "text-j-info";
    case "medium":
      return "text-j-warning";
    case "high":
      return "text-j-error";
    case "critical":
      return "text-j-error";
    default:
      return "text-t-muted";
  }
}

/** Canonical, Title-case display labels for execution/task statuses */
export const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  executing: "Executing",
  in_progress: "In progress",
  completed: "Completed",
  approved: "Approved",
  awaiting_approval: "Awaiting approval",
  pending_approval: "Awaiting approval",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  paused: "Paused",
  proposal: "Proposal",
};

/** Title-case display label for a status, falling back to the raw value */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** Maps frame kind to badge styling (bg + text classes) */
export function kindStyle(kind: string): { bg: string; text: string } {
  switch (kind) {
    case "proposal":
      return { bg: "bg-j-secondary-soft", text: "text-j-secondary" };
    case "finding":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "run":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "briefing":
      return { bg: "bg-j-success-soft", text: "text-j-success" };
    case "record":
      return { bg: "bg-surface-3", text: "text-t-secondary" };
    default:
      return { bg: "bg-surface-3", text: "text-t-secondary" };
  }
}

/** Maps frame status to a dot colour.
 *
 *  FrameStatus is a DIFFERENT vocabulary from the execution/task statuses
 *  `statusColor` covers: `needs_you` would fall to its grey default. Kept
 *  separate rather than merged so neither vocabulary silently absorbs the
 *  other's fallbacks. */
export function frameStatusColor(status: string): string {
  switch (status) {
    case "needs_you":
      return "bg-j-warning";
    case "running":
      return "bg-j-info";
    case "done":
      return "bg-j-success";
    case "failed":
      return "bg-j-error";
    case "new":
      return "bg-j-secondary";
    default:
      return "bg-t-muted";
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

/** Maps search source DB slug to a friendly label */
export function sourceDbLabel(db: string): string {
  switch (db) {
    case "qdrant":
      return "Vector";
    case "postgres_fts":
      return "Keyword";
    case "neo4j":
      return "Graph";
    default:
      return db;
  }
}

/** Canonical source-db badge descriptor: friendly label + token style classes */
export interface SourceDbBadge {
  label: string;
  style: string;
}

/** Single source of truth for rendering a search result's source-db badge */
export function sourceDbBadge(db: string): SourceDbBadge {
  return { label: sourceDbLabel(db), style: sourceDbStyle(db) };
}

/** Human-readable labels for trust levels */
export const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

/** Human-readable labels for frame kinds */
export const KIND_LABELS: Record<string, string> = {
  proposal: "Proposal",
  finding: "Finding",
  run: "Run",
  record: "Record",
  briefing: "Briefing",
};

/** Human-readable labels for frame statuses.
 *
 *  Deliberately not merged into STATUS_LABELS: that map is the execution/task
 *  vocabulary, and a frame status printed through it falls through raw —
 *  `needs_you` lowercase-with-underscore beside Title-case neighbours. */
export const FRAME_STATUS_LABELS: Record<string, string> = {
  needs_you: "Needs you",
  scheduled: "Scheduled",
  running: "Running",
  done: "Done",
  failed: "Failed",
  new: "New",
  seen: "Seen",
};

/**
 * What the events of a unit are called. A meeting is not a message, and the
 * fallback has to survive an entity type nobody has mapped yet, so it is a
 * noun that is true of anything a connector can group.
 */
export const EVENT_NOUN: Record<string, string> = {
  email_thread: "messages",
  meeting: "events",
  issue: "updates",
  pull_request: "updates",
  channel: "messages",
  page: "revisions",
};

export const DEFAULT_EVENT_NOUN = "updates";

/**
 * A count of one says nothing the card does not already show, and it is where
 * the wrong noun was on display. Lives here rather than in either view: the
 * card and the detail modal are two projections of one frame, and they read
 * `1 message` on a meeting for as long as each owned its own copy.
 */
export function eventCountLabel(entityType: string, count: number): string | null {
  if (count <= 1) return null;
  return `${count} ${EVENT_NOUN[entityType] ?? DEFAULT_EVENT_NOUN}`;
}

/** Generated surface types for the A2UI system. */

import type { A2UISurface } from "@/lib/a2ui-types";

export type SurfaceKind =
  | "summary"
  | "briefing"
  | "plan"
  | "checklist"
  | "approval"
  | "comparison"
  | "alert"
  | "timeline"
  | "table"
  | "recommendation"
  | "activity";

export type SurfacePosition = "inline" | "right-pane" | "center-pane" | "workspace";

export interface GeneratedSurface {
  id: string;
  kind: SurfaceKind;
  title: string;
  data: Record<string, unknown> & { a2ui_surface?: A2UISurface };
  created_at: string;
  pinned: boolean;
  position: SurfacePosition;
  schema_version: number;
  source_message_id: string | null;
  source_run_id: string | null;
  source_artifact_id: string | null;
}

export interface SummaryData {
  text: string;
  highlights: string[];
}

export interface ChecklistItem {
  label: string;
  checked: boolean;
}

export interface ComparisonRow {
  label: string;
  values: string[];
}

export interface TimelineEntry {
  timestamp: string;
  label: string;
  detail: string | null;
}

export interface TableColumn {
  key: string;
  label: string;
}

export interface TableData {
  columns: TableColumn[];
  rows: Record<string, unknown>[];
}

export interface PlanTask {
  task_type: string;
  input_data?: { description?: string } & Record<string, unknown>;
}

export interface PlanData {
  goal?: string;
  tasks: PlanTask[];
}

export interface ApprovalData {
  risk_level?: string;
  summary?: string;
  impact?: string;
  reversibility?: string;
}

export interface AlertData {
  level?: string;
  title?: string;
  message?: string;
}

export interface BriefingData {
  headline?: string;
  full_text?: string;
  sections?: Array<{ title: string; content: string }>;
}

export interface ComparisonData {
  options: string[];
  rows: ComparisonRow[];
}

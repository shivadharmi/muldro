/** Context sidebar types — mirrors backend command_context schemas. */

export interface EntityRef {
  entity_id: string;
  name: string;
  entity_type: string;
  relevance: number;
}

export interface MemoryRef {
  memory_id: string;
  content: string;
  memory_type: string;
  relevance: number;
}

export interface SourceRef {
  source_type: "trace" | "artifact" | "integration" | "observation";
  source_id: string;
  label: string;
  url: string | null;
}

export interface EvidenceBundle {
  entities: EntityRef[];
  memories: MemoryRef[];
  sources: SourceRef[];
  route_info: Record<string, unknown> | null;
  confidence: number | null;
  risk_level: string | null;
}

export interface ContextSidebarData {
  message_id: string | null;
  conversation_id: string | null;
  evidence: EvidenceBundle;
  active_run: Record<string, unknown> | null;
  timestamp: string | null;
}

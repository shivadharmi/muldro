"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchKnowledgeMemories } from "@/lib/api";
import type {
  KnowledgeGraphNode,
  KnowledgeGraphEdge,
  KnowledgeMemoryItem,
} from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";

// ── Color mappings ─────────────────────────────────────────────

const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "hsl(193 100% 66%)",
  organization: "hsl(247 92% 74%)",
  project: "hsl(159 78% 54%)",
  document: "hsl(36 100% 64%)",
  repository: "hsl(351 100% 71%)",
};

const MEMORY_TYPE_COLORS: Record<string, string> = {
  semantic: "hsl(247 92% 74%)",
  episodic: "hsl(193 100% 66%)",
  preference: "hsl(36 100% 64%)",
  goal: "hsl(159 78% 54%)",
  relationship: "hsl(214 16% 58%)",
};

function getEntityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type.toLowerCase()] ?? "hsl(214 16% 58%)";
}

function getMemoryColor(type: string): string {
  return MEMORY_TYPE_COLORS[type.toLowerCase()] ?? "hsl(214 16% 58%)";
}

// ── Helpers ────────────────────────────────────────────────────

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function formatTimeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const seconds = Math.floor((now - then) / 1000);

  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

function truncateText(text: string, maxLen: number): string {
  return text.length > maxLen ? text.slice(0, maxLen) + "\u2026" : text;
}

// ── Connection helpers ─────────────────────────────────────────

interface Connection {
  entityId: string;
  name: string;
  type: string;
  relationType: string;
}

function findConnections(
  entityId: string,
  edges: KnowledgeGraphEdge[],
  nodes: KnowledgeGraphNode[],
): Connection[] {
  const nodeMap = new Map(nodes.map((n) => [n.entity_id, n]));
  const connections: Connection[] = [];

  for (const edge of edges) {
    const sourceId = edge.from_entity_id ?? edge.from;
    const targetId = edge.to_entity_id ?? edge.to;
    const relType = edge.relation_type ?? edge.type ?? "related";

    if (sourceId === entityId && targetId) {
      const target = nodeMap.get(targetId);
      if (target) {
        connections.push({
          entityId: targetId,
          name: target.canonical_name,
          type: target.entity_type,
          relationType: relType,
        });
      }
    } else if (targetId === entityId && sourceId) {
      const source = nodeMap.get(sourceId);
      if (source) {
        connections.push({
          entityId: sourceId,
          name: source.canonical_name,
          type: source.entity_type,
          relationType: relType,
        });
      }
    }
  }

  return connections;
}

// ── Component ──────────────────────────────────────────────────

export function GraphDetailPanel() {
  const selectedEntityId = useKnowledgeStore((s) => s.selectedEntityId);
  const graphData = useKnowledgeStore((s) => s.graphData);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);

  const selectedNode = graphData.nodes.find(
    (n) => n.entity_id === selectedEntityId,
  );

  const { data: memoriesData } = useQuery({
    queryKey: ["knowledge-memories-for-entity", selectedEntityId],
    queryFn: () =>
      fetchKnowledgeMemories({ entity_id: selectedEntityId!, limit: 5 }),
    enabled: !!selectedEntityId,
  });

  if (!selectedEntityId || !selectedNode) {
    return (
      <div className="w-80 bg-surface-1 border-l border-b-secondary flex items-center justify-center h-full">
        <p className="text-sm text-t-tertiary">Select a node to view details</p>
      </div>
    );
  }

  const connections = findConnections(
    selectedEntityId,
    graphData.edges,
    graphData.nodes,
  );
  const memories = memoriesData?.items ?? [];
  const color = getEntityColor(selectedNode.entity_type);
  const attributes = selectedNode.attributes
    ? Object.entries(selectedNode.attributes)
    : [];

  return (
    <div className="w-80 bg-surface-1 border-l border-b-secondary overflow-y-auto h-full">
      {/* Header */}
      <div className="p-4 border-b border-b-secondary">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3 min-w-0">
            {/* Avatar circle */}
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold shrink-0"
              style={{
                backgroundColor: `${color}20`,
                color,
              }}
            >
              {getInitials(selectedNode.canonical_name)}
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-t-primary truncate">
                {selectedNode.canonical_name}
              </h3>
              <span
                className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium mt-1 capitalize"
                style={{
                  backgroundColor: `${color}15`,
                  color,
                  border: `1px solid ${color}30`,
                }}
              >
                {selectedNode.entity_type}
              </span>
            </div>
          </div>
          {/* Close button */}
          <button
            type="button"
            onClick={() => selectEntity(null)}
            className="text-t-muted hover:text-t-primary transition-colors cursor-pointer p-1"
            aria-label="Close detail panel"
          >
            <span className="text-lg leading-none">&times;</span>
          </button>
        </div>

        {/* Importance score */}
        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs text-t-muted">Importance</span>
          <div className="flex-1 h-1.5 rounded-full bg-surface-3 overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.round(selectedNode.importance_score * 100)}%`,
                backgroundColor: color,
              }}
            />
          </div>
          <span className="text-xs text-t-secondary tabular-nums">
            {(selectedNode.importance_score * 100).toFixed(0)}%
          </span>
        </div>
      </div>

      {/* Attributes */}
      {attributes.length > 0 && (
        <div className="p-4 border-b border-b-secondary">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Attributes
          </h4>
          <div className="space-y-1.5">
            {attributes.map(([key, value]) => (
              <div key={key} className="flex items-start gap-2 text-xs">
                <span className="text-t-muted shrink-0">{key}:</span>
                <span className="text-t-secondary break-all">
                  {String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Metadata */}
      <div className="p-4 border-b border-b-secondary">
        <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
          Metadata
        </h4>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-[var(--radius-md)] bg-surface-2 p-2">
            <p className="text-xs text-t-muted">Interactions</p>
            <p className="text-sm font-medium text-t-primary tabular-nums">
              {selectedNode.interaction_count}
            </p>
          </div>
          <div className="rounded-[var(--radius-md)] bg-surface-2 p-2">
            <p className="text-xs text-t-muted">Last seen</p>
            <p className="text-sm font-medium text-t-primary">
              {selectedNode.last_seen_at
                ? formatTimeAgo(selectedNode.last_seen_at)
                : "--"}
            </p>
          </div>
        </div>
      </div>

      {/* Connections */}
      {connections.length > 0 && (
        <div className="p-4 border-b border-b-secondary">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Connections ({connections.length})
          </h4>
          <div className="space-y-1">
            {connections.map((conn) => (
              <button
                key={conn.entityId}
                type="button"
                onClick={() => selectEntity(conn.entityId)}
                className="w-full text-left px-2 py-1.5 rounded-md hover:bg-surface-2 cursor-pointer transition-colors flex items-center gap-2"
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: getEntityColor(conn.type) }}
                />
                <span className="text-xs text-t-primary truncate flex-1">
                  {conn.name}
                </span>
                <span className="text-xs text-t-muted shrink-0 capitalize">
                  {conn.relationType.replace(/_/g, " ")}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Related Memories */}
      {memories.length > 0 && (
        <div className="p-4 border-b border-b-secondary">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Related Memories ({memories.length})
          </h4>
          <div className="space-y-2">
            {memories.map((mem: KnowledgeMemoryItem) => (
              <button
                key={mem.memory_id}
                type="button"
                onClick={() => {
                  setActiveTab("memories");
                  selectMemory(mem.memory_id);
                }}
                className="w-full text-left p-2 rounded-[var(--radius-md)] bg-surface-2 hover:bg-surface-3 cursor-pointer transition-colors"
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{
                      backgroundColor: getMemoryColor(mem.memory_type),
                    }}
                  />
                  <span className="text-xs text-t-muted capitalize">
                    {mem.memory_type}
                  </span>
                  <span className="text-xs text-t-muted ml-auto tabular-nums">
                    {(mem.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="text-xs text-t-secondary leading-relaxed">
                  {truncateText(mem.fact_text, 100)}
                </p>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Aliases */}
      {(selectedNode.aliases ?? []).length > 0 && (
        <div className="p-4">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Aliases
          </h4>
          <div className="flex flex-wrap gap-1">
            {(selectedNode.aliases ?? []).map((alias, idx) => {
              const text = typeof alias === "string" ? alias : (alias as { alias?: string }).alias ?? String(alias);
              return (
                <span
                  key={`${text}-${idx}`}
                  className="px-2 py-0.5 rounded-full bg-surface-2 text-xs text-t-secondary"
                >
                  {text}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

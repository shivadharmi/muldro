"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { fetchKnowledgeMemoryDetail } from "@/lib/api";
import type { KnowledgeMemoryDetail } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { getStoredToken } from "@/lib/auth";

// ── Memory type styling ──────────────────────────────────────────

const MEMORY_TYPE_STYLES: Record<string, { colorClass: string; bgClass: string; label: string }> = {
  semantic:      { colorClass: "text-j-secondary",  bgClass: "bg-j-secondary-soft", label: "Semantic Memory" },
  episodic:      { colorClass: "text-j-primary",    bgClass: "bg-j-primary-soft",   label: "Episodic Memory" },
  preference:    { colorClass: "text-j-warning",    bgClass: "bg-j-warning-soft",   label: "Preference" },
  goal:          { colorClass: "text-j-accent",     bgClass: "bg-j-accent-soft",    label: "Goal" },
  relationship:  { colorClass: "text-t-muted",      bgClass: "bg-surface-3",        label: "Relationship" },
  procedural:    { colorClass: "text-t-tertiary",   bgClass: "bg-surface-3",        label: "Procedural" },
  task_context:  { colorClass: "text-j-error",      bgClass: "bg-j-error-soft",     label: "Task Context" },
  briefing_item: { colorClass: "text-j-primary",    bgClass: "bg-j-primary-soft",   label: "Briefing Item" },
};

function getTypeStyle(memoryType: string) {
  return MEMORY_TYPE_STYLES[memoryType] ?? { colorClass: "text-t-muted", bgClass: "bg-surface-3", label: memoryType };
}

// ── Entity type colors ───────────────────────────────────────────

const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "hsl(193 100% 66%)",
  organization: "hsl(247 92% 74%)",
  project: "hsl(159 78% 54%)",
  document: "hsl(36 100% 64%)",
  repository: "hsl(351 100% 71%)",
};

function getEntityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type.toLowerCase()] ?? "hsl(214 16% 58%)";
}

// ── Helpers ───────────────────────────────────────────────────────

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleString();
}

function formatTtl(expiresAt: string | null): string {
  if (!expiresAt) return "Permanent";
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (diff <= 0) return "Expired";
  const hours = Math.floor(diff / 3600000);
  if (hours < 24) return `${hours}h remaining`;
  const days = Math.floor(hours / 24);
  return `${days}d remaining`;
}

// ── Component ─────────────────────────────────────────────────────

export function MemoryDetailPanel() {
  const selectedMemoryId = useKnowledgeStore((s) => s.selectedMemoryId);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);

  const queryClient = useQueryClient();

  const { data: detail, isLoading } = useQuery({
    queryKey: ["knowledge-memory-detail", selectedMemoryId],
    queryFn: () => fetchKnowledgeMemoryDetail(selectedMemoryId!),
    enabled: !!selectedMemoryId,
  });

  const archiveMutation = useMutation({
    mutationFn: async (memoryId: string) => {
      const token = getStoredToken() || process.env.NEXT_PUBLIC_API_TOKEN || "";
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`/api/memories/${memoryId}`, {
        method: "DELETE",
        headers,
      });
      if (!res.ok) throw new Error("Failed to archive memory");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-memories"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-memory-detail"] });
      selectMemory(null);
    },
  });

  if (!selectedMemoryId) {
    return null;
  }

  if (isLoading) {
    return (
      <div className="absolute inset-0 z-20 lg:relative lg:inset-auto lg:z-auto w-full lg:w-80 bg-surface-1 lg:border-l border-b-secondary flex items-center justify-center h-full">
        <p className="text-sm text-t-tertiary">Loading...</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="absolute inset-0 z-20 lg:relative lg:inset-auto lg:z-auto w-full lg:w-80 bg-surface-1 lg:border-l border-b-secondary flex items-center justify-center h-full">
        <p className="text-sm text-t-tertiary">Memory not found</p>
      </div>
    );
  }

  const style = getTypeStyle(detail.memory_type);

  return (
    <div className="absolute inset-0 z-20 lg:relative lg:inset-auto lg:z-auto w-full lg:w-80 bg-surface-1 lg:border-l border-b-secondary overflow-y-auto h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-b-secondary">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div
              className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold ${style.bgClass} ${style.colorClass}`}
            >
              {detail.memory_type.charAt(0).toUpperCase()}
            </div>
            <span className={`text-sm font-medium ${style.colorClass}`}>
              {style.label}
            </span>
          </div>
          <button
            type="button"
            onClick={() => selectMemory(null)}
            className="text-t-muted hover:text-t-primary transition-colors cursor-pointer p-2 -mr-1 min-w-[44px] min-h-[44px] flex items-center justify-center"
            aria-label="Close detail panel"
          >
            <span className="text-lg leading-none">&times;</span>
          </button>
        </div>
      </div>

      {/* Full text */}
      <div className="px-4 py-3 border-b border-b-secondary">
        <p className="text-sm text-t-primary leading-relaxed">
          {detail.fact_text}
        </p>
      </div>

      {/* Properties */}
      <PropertySection detail={detail} />

      {/* Linked Entities */}
      {detail.linked_entities.length > 0 && (
        <div className="px-4 py-3 border-b border-b-secondary">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Linked Entities
          </h4>
          <div className="space-y-1">
            {detail.linked_entities.map((entity) => (
              <button
                key={entity.entity_id}
                type="button"
                onClick={() => {
                  setActiveTab("graph");
                  selectEntity(entity.entity_id);
                }}
                className="w-full text-left px-2 py-2 rounded-md hover:bg-surface-2 cursor-pointer transition-colors flex items-center gap-2 min-h-[44px]"
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: getEntityColor(entity.entity_type) }}
                />
                <span className="text-xs text-t-primary truncate flex-1">
                  {entity.canonical_name}
                </span>
                <span className="text-xs text-t-muted shrink-0 capitalize">
                  {entity.entity_type}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Provenance */}
      {detail.provenance.source_description && (
        <div className="px-4 py-3 border-b border-b-secondary">
          <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
            Provenance
          </h4>
          <div className="border-l-2 border-b-secondary bg-surface-2 px-3 py-2 rounded-r-md">
            <p className="text-sm text-t-secondary leading-relaxed">
              {detail.provenance.source_description}
            </p>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="px-4 py-3">
        <div className="flex gap-2">
          {detail.linked_entities.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setActiveTab("graph");
                selectEntity(detail.linked_entities[0].entity_id);
              }}
              className="flex-1 px-3 py-2 text-xs font-medium rounded-md bg-surface-2 text-t-secondary hover:bg-surface-3 transition-colors cursor-pointer min-h-[44px]"
            >
              View in Graph
            </button>
          )}
          <button
            type="button"
            onClick={() => archiveMutation.mutate(detail.memory_id)}
            disabled={archiveMutation.isPending}
            className="flex-1 px-3 py-2 text-xs font-medium rounded-md bg-j-error-soft text-j-error hover:opacity-80 transition-opacity cursor-pointer disabled:opacity-50 min-h-[44px]"
          >
            {archiveMutation.isPending ? "Archiving..." : "Archive"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Property section (extracted for readability) ──────────────────

function PropertySection({ detail }: { detail: KnowledgeMemoryDetail }) {
  const properties = [
    { label: "Confidence", value: `${(detail.confidence * 100).toFixed(0)}%` },
    { label: "Stability", value: `${(detail.stability_score * 100).toFixed(0)}%` },
    { label: "Refreshes", value: String(detail.refresh_count) },
    { label: "Created", value: formatDate(detail.created_at) },
    { label: "Last Accessed", value: formatDate(detail.last_accessed_at) },
    { label: "Scope", value: detail.scope ?? "--" },
    { label: "TTL", value: formatTtl(detail.expires_at) },
  ];

  return (
    <div className="px-4 py-3 border-b border-b-secondary">
      <h4 className="text-xs font-medium text-t-muted uppercase tracking-wider mb-2">
        Properties
      </h4>
      <div className="space-y-1.5">
        {properties.map((prop) => (
          <div key={prop.label} className="flex items-center justify-between text-xs">
            <span className="text-t-muted">{prop.label}</span>
            <span className="text-t-secondary tabular-nums">{prop.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

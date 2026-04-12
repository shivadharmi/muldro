import { create } from "zustand";

import type { KnowledgeGraphNode, KnowledgeGraphEdge } from "@/lib/api";

interface GraphData {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

/** Derive a stable dedup key from an edge (handles both Neo4j and enriched shapes). */
function edgeKey(e: KnowledgeGraphEdge): string {
  const src = e.from_entity_id ?? e.from ?? "";
  const tgt = e.to_entity_id ?? e.to ?? "";
  return `${src}-${tgt}`;
}

type KnowledgeTab = "graph" | "memories" | "stats";
type MemorySort = "recent" | "confidence" | "stability";

interface KnowledgeState {
  // Tab
  activeTab: KnowledgeTab;
  setActiveTab: (tab: KnowledgeTab) => void;

  // Graph
  graphData: GraphData;
  setGraphData: (data: GraphData) => void;
  mergeGraphData: (data: GraphData) => void;
  selectedEntityId: string | null;
  selectEntity: (id: string | null) => void;
  expandedNodes: Set<string>;
  markExpanded: (id: string) => void;
  hiddenTypes: Set<string>;
  toggleTypeFilter: (type: string) => void;

  // Memories
  selectedMemoryId: string | null;
  selectMemory: (id: string | null) => void;
  memoryTypeFilter: string | null;
  setMemoryTypeFilter: (type: string | null) => void;
  memorySortBy: MemorySort;
  setMemorySortBy: (sort: MemorySort) => void;

  // Search
  searchQuery: string;
  setSearchQuery: (q: string) => void;
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  // Tab
  activeTab: "graph",
  setActiveTab: (tab) => set({ activeTab: tab }),

  // Graph
  graphData: { nodes: [], edges: [] },
  setGraphData: (data) => set({ graphData: data }),
  mergeGraphData: (data) => {
    const current = get().graphData;
    const existingNodeIds = new Set(current.nodes.map((n) => n.entity_id));
    const newNodes = data.nodes.filter(
      (n) => !existingNodeIds.has(n.entity_id)
    );

    const existingEdgeKeys = new Set(current.edges.map(edgeKey));
    const newEdges = data.edges.filter(
      (e) => !existingEdgeKeys.has(edgeKey(e))
    );

    set({
      graphData: {
        nodes: [...current.nodes, ...newNodes],
        edges: [...current.edges, ...newEdges],
      },
    });
  },
  selectedEntityId: null,
  selectEntity: (id) => set({ selectedEntityId: id }),
  expandedNodes: new Set(),
  markExpanded: (id) => {
    const next = new Set(get().expandedNodes);
    next.add(id);
    set({ expandedNodes: next });
  },
  hiddenTypes: new Set(),
  toggleTypeFilter: (type) => {
    const next = new Set(get().hiddenTypes);
    if (next.has(type)) {
      next.delete(type);
    } else {
      next.add(type);
    }
    set({ hiddenTypes: next });
  },

  // Memories
  selectedMemoryId: null,
  selectMemory: (id) => set({ selectedMemoryId: id }),
  memoryTypeFilter: null,
  setMemoryTypeFilter: (type) => set({ memoryTypeFilter: type }),
  memorySortBy: "recent",
  setMemorySortBy: (sort) => set({ memorySortBy: sort }),

  // Search
  searchQuery: "",
  setSearchQuery: (q) => set({ searchQuery: q }),
}));

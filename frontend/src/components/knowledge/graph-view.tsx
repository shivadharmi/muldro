"use client";

import dynamic from "next/dynamic";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { KnowledgeGraphNode, KnowledgeGraphEdge } from "@/lib/api";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { EmptyState } from "@/components/ui/empty-state";
import { GraphContextMenu } from "./graph-context-menu";

// ── Dynamic import (SSR-safe) ─────────────────────────────────
// react-force-graph-2d uses Canvas APIs unavailable in SSR

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

// ── CSS variable resolver (Canvas cannot use var()) ───────────

function resolveCssVar(varExpr: string): string {
  if (typeof window === "undefined") return "#888";
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(varExpr.replace("var(", "").replace(")", ""))
      .trim() || "#888"
  );
}

// ── Entity type colors ────────────────────────────────────────

const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "var(--muldro-primary)",
  organization: "var(--muldro-secondary)",
  project: "var(--muldro-accent)",
  document: "var(--muldro-warning)",
  repository: "var(--muldro-error)",
};

const DEFAULT_NODE_COLOR = "var(--muldro-text-muted)";
const SELECTED_GLOW_COLOR_VAR = "var(--muldro-primary)";
const LINK_DEFAULT_COLOR = "rgba(120, 130, 150, 0.3)";
const LINK_HIGHLIGHT_COLOR = "var(--muldro-primary)";
const LABEL_COLOR = "rgba(200, 210, 220, 0.9)";

function getNodeColor(type: string): string {
  return resolveCssVar(ENTITY_TYPE_COLORS[type.toLowerCase()] ?? DEFAULT_NODE_COLOR);
}

function getNodeRadius(importanceScore: number): number {
  return Math.min(16, Math.max(4, 4 + importanceScore * 12));
}

// ── Transformed types for react-force-graph ───────────────────

interface GraphNode {
  id: string;
  canonical_name: string;
  entity_type: string;
  importance_score: number;
  interaction_count: number;
  entity_id: string;
  x?: number;
  y?: number;
}

interface GraphLink {
  source: string;
  target: string;
  relation_type: string;
}

interface ContextMenuState {
  x: number;
  y: number;
  entityId: string;
  entityName: string;
}

// ── Component ─────────────────────────────────────────────────

export function GraphView() {
  const graphData = useKnowledgeStore((s) => s.graphData);
  const selectedEntityId = useKnowledgeStore((s) => s.selectedEntityId);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);
  const hiddenTypes = useKnowledgeStore((s) => s.hiddenTypes);
  const mergeGraphData = useKnowledgeStore((s) => s.mergeGraphData);
  const markExpanded = useKnowledgeStore((s) => s.markExpanded);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const setMemoryTypeFilter = useKnowledgeStore((s) => s.setMemoryTypeFilter);
  const setGraphData = useKnowledgeStore((s) => s.setGraphData);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(
    null,
  );

  // ── Container resize tracking ───────────────────────────────

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        const { width, height } = entry.contentRect;
        setDimensions({ width: Math.floor(width), height: Math.floor(height) });
      }
    });

    observer.observe(container);

    // Set initial dimensions
    const rect = container.getBoundingClientRect();
    setDimensions({
      width: Math.floor(rect.width),
      height: Math.floor(rect.height),
    });

    return () => observer.disconnect();
  }, []);

  // ── Transform data for react-force-graph ────────────────────

  const transformedData = useMemo(() => {
    const visibleNodes: GraphNode[] = graphData.nodes
      .filter((n: KnowledgeGraphNode) => !hiddenTypes.has(n.entity_type.toLowerCase()))
      .map((n: KnowledgeGraphNode) => ({
        id: n.entity_id,
        canonical_name: n.canonical_name,
        entity_type: n.entity_type,
        importance_score: n.importance_score,
        interaction_count: n.interaction_count,
        entity_id: n.entity_id,
      }));

    const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));

    const visibleLinks: GraphLink[] = graphData.edges
      .map((e: KnowledgeGraphEdge) => ({
        source: e.from_entity_id ?? e.from ?? "",
        target: e.to_entity_id ?? e.to ?? "",
        relation_type: e.relation_type ?? e.type ?? "related",
      }))
      .filter(
        (l) =>
          visibleNodeIds.has(l.source) && visibleNodeIds.has(l.target),
      );

    return { nodes: visibleNodes, links: visibleLinks };
  }, [graphData.nodes, graphData.edges, hiddenTypes]);

  // ── Node canvas renderer ────────────────────────────────────

  const drawNode = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const gNode = node as GraphNode;
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const radius = getNodeRadius(gNode.importance_score);
      const color = getNodeColor(gNode.entity_type);
      const isSelected = gNode.entity_id === selectedEntityId;

      // Glow ring for selected node
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(x, y, radius + 3, 0, 2 * Math.PI);
        const glowColor = resolveCssVar(SELECTED_GLOW_COLOR_VAR);
        ctx.fillStyle = glowColor + "66"; // ~40% alpha
        ctx.fill();
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();

      // Label
      const label =
        gNode.canonical_name.length > 16
          ? gNode.canonical_name.slice(0, 16) + "\u2026"
          : gNode.canonical_name;
      const fontSize = Math.max(3, 12 / globalScale);
      ctx.font = `${fontSize}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = LABEL_COLOR;
      ctx.fillText(label, x, y + radius + 2);
    },
    [selectedEntityId],
  );

  // ── Pointer area (hit detection) ────────────────────────────

  const paintPointerArea = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any, areaColor: string, ctx: CanvasRenderingContext2D) => {
      const gNode = node as GraphNode;
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const radius = getNodeRadius(gNode.importance_score) + 2;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = areaColor;
      ctx.fill();
    },
    [],
  );

  // ── Interaction handlers ────────────────────────────────────

  const handleNodeClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      selectEntity((node as GraphNode).entity_id);
      setContextMenu(null);
    },
    [selectEntity],
  );

  const handleNodeRightClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any, event: MouseEvent) => {
      event.preventDefault();
      const gNode = node as GraphNode;
      setContextMenu({
        x: event.clientX,
        y: event.clientY,
        entityId: gNode.entity_id,
        entityName: gNode.canonical_name,
      });
    },
    [],
  );

  const handleBackgroundClick = useCallback(() => {
    selectEntity(null);
    setContextMenu(null);
  }, [selectEntity]);

  // ── Link styling callbacks ──────────────────────────────────

  const linkWidth = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (link: any) => {
      if (!selectedEntityId) return 0.5;
      const sourceId =
        typeof link.source === "object" ? link.source.id : link.source;
      const targetId =
        typeof link.target === "object" ? link.target.id : link.target;
      return sourceId === selectedEntityId || targetId === selectedEntityId
        ? 2
        : 0.5;
    },
    [selectedEntityId],
  );

  const linkColor = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (link: any) => {
      if (!selectedEntityId) return LINK_DEFAULT_COLOR;
      const sourceId =
        typeof link.source === "object" ? link.source.id : link.source;
      const targetId =
        typeof link.target === "object" ? link.target.id : link.target;
      return sourceId === selectedEntityId || targetId === selectedEntityId
        ? resolveCssVar(LINK_HIGHLIGHT_COLOR)
        : LINK_DEFAULT_COLOR;
    },
    [selectedEntityId],
  );

  // ── Context menu actions ────────────────────────────────────

  const handleFocus = useCallback(
    (entityId: string) => {
      const node = transformedData.nodes.find((n) => n.id === entityId);
      if (node && graphRef.current) {
        graphRef.current.centerAt(node.x, node.y, 1000);
        graphRef.current.zoom(2, 1000);
      }
    },
    [transformedData.nodes],
  );

  const handleExpand = useCallback(
    async (entityId: string, depth: number) => {
      try {
        const res = await fetch(
          `/api/graph/${entityId}/traverse?depth=${depth}`,
        );
        if (res.ok) {
          const data = await res.json();
          const nodes: KnowledgeGraphNode[] = data.nodes ?? [];
          const edges: KnowledgeGraphEdge[] = data.edges ?? [];
          mergeGraphData({ nodes, edges });
          markExpanded(entityId);
        }
      } catch {
        // Silently handle fetch failures
      }
    },
    [mergeGraphData, markExpanded],
  );

  const handleHide = useCallback(
    (entityId: string) => {
      const filteredNodes = graphData.nodes.filter(
        (n) => n.entity_id !== entityId,
      );
      const filteredEdges = graphData.edges.filter((e) => {
        const src = e.from_entity_id ?? e.from;
        const tgt = e.to_entity_id ?? e.to;
        return src !== entityId && tgt !== entityId;
      });
      setGraphData({ nodes: filteredNodes, edges: filteredEdges });
      if (selectedEntityId === entityId) {
        selectEntity(null);
      }
    },
    [graphData, setGraphData, selectedEntityId, selectEntity],
  );

  const handleViewMemories = useCallback(
    (entityId: string) => {
      selectEntity(entityId);
      setActiveTab("memories");
      setMemoryTypeFilter(null);
    },
    [selectEntity, setActiveTab, setMemoryTypeFilter],
  );

  // ── Render ──────────────────────────────────────────────────

  return (
    <div ref={containerRef} className="relative w-full h-full min-h-[400px]">
      <ForceGraph2D
        ref={graphRef}
        graphData={transformedData}
        nodeId="id"
        width={dimensions.width}
        height={dimensions.height}
        nodeCanvasObject={drawNode}
        nodeCanvasObjectMode={() => "replace"}
        nodePointerAreaPaint={paintPointerArea}
        onNodeClick={handleNodeClick}
        onNodeRightClick={handleNodeRightClick}
        onBackgroundClick={handleBackgroundClick}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        linkWidth={linkWidth}
        linkColor={linkColor}
        backgroundColor="transparent"
        cooldownTicks={100}
        enableNodeDrag
      />

      {/* Context Menu */}
      {contextMenu && (
        <GraphContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          entityId={contextMenu.entityId}
          entityName={contextMenu.entityName}
          onClose={() => setContextMenu(null)}
          onFocus={handleFocus}
          onExpand={handleExpand}
          onHide={handleHide}
          onViewMemories={handleViewMemories}
        />
      )}

      {/* Empty state */}
      {transformedData.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <EmptyState
            title="No graph data"
            description="Connect sources and interact with Muldro to build the knowledge graph"
          />
        </div>
      )}
    </div>
  );
}

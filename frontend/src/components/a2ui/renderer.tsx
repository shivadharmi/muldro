"use client";

import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";
import { ErrorBoundary } from "@/components/error-boundary";
import { A2UIAlert } from "./components/alert";
import { A2UIBadge } from "./components/badge";
import { A2UIButton } from "./components/button";
import { A2UICard } from "./components/card";
import { A2UICodeBlock } from "./components/code-block";
import { A2UIDivider } from "./components/divider";
import { A2UIEntityCard } from "./components/entity-card";
import { A2UIExecutionTrace } from "./components/execution-trace";
import { A2UIList } from "./components/list";
import { A2UIMemoryCard } from "./components/memory-card";
import { A2UIMetric } from "./components/metric";
import { A2UIProgress } from "./components/progress";
import { A2UIRow } from "./components/row";
import { A2UITable } from "./components/a2ui-table";
import { A2UIText } from "./components/text";
import { A2UITimeline } from "./components/timeline";

interface RendererProps {
  surface: A2UISurface;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

/**
 * Maximum nesting depth the renderer will descend. A2UI trees are author-shallow
 * (card > column > row > text ≈ 5 levels); this caps untrusted, LLM-authored
 * surface_data so a pathologically deep (or cyclic-looking) tree can't blow the
 * React stack. Trees deeper than this render a truncation placeholder at the cap.
 */
const MAX_RENDER_DEPTH = 24;

/** Maps A2UI component types to React implementations, wrapped in ErrorBoundary. */
function renderComponent(
  component: A2UIComponent,
  onAction: (action: string, payload: Record<string, unknown>) => void,
  depth: number
): React.ReactNode {
  if (depth > MAX_RENDER_DEPTH) {
    return (
      <div
        key={`depth-${component.id}`}
        className="p-2 text-xs text-t-tertiary border border-j-warning/30 rounded-[var(--radius-sm)]"
      >
        Content nested too deeply to display
      </div>
    );
  }
  return (
    <ErrorBoundary
      key={`eb-${component.id}`}
      fallback={
        <div className="p-2 text-xs text-t-tertiary border border-j-warning/30 rounded-[var(--radius-sm)]">
          Failed to render {component.type ?? "unknown"} component
        </div>
      }
    >
      {renderComponentInner(component, onAction, depth)}
    </ErrorBoundary>
  );
}

/** Inner render dispatch — errors caught by the wrapping ErrorBoundary. */
function renderComponentInner(
  component: A2UIComponent,
  onAction: (action: string, payload: Record<string, unknown>) => void,
  depth: number
): React.ReactNode {
  const children = component.children?.map((child) => renderComponent(child, onAction, depth + 1));

  switch (component.type) {
    // Text
    case "Text":
      return <A2UIText key={component.id} component={component} />;
    case "CodeBlock":
      return <A2UICodeBlock key={component.id} component={component} />;
    case "Badge":
      return <A2UIBadge key={component.id} component={component} />;
    case "Alert":
      return <A2UIAlert key={component.id} component={component} />;

    // Layout
    case "Card":
      return <A2UICard key={component.id} component={component}>{children}</A2UICard>;
    case "Row":
      return <A2UIRow key={component.id} component={component}>{children}</A2UIRow>;
    case "List":
      return <A2UIList key={component.id} component={component}>{children}</A2UIList>;
    case "Divider":
      return <A2UIDivider key={component.id} component={component} />;

    // Input
    case "Button":
      return <A2UIButton key={component.id} component={component} onAction={onAction} />;

    // Data
    case "Table":
      return <A2UITable key={component.id} component={component} />;
    case "Timeline":
      return <A2UITimeline key={component.id} component={component} />;
    case "Metric":
      return <A2UIMetric key={component.id} component={component} />;
    case "Progress":
      return <A2UIProgress key={component.id} component={component} />;

    // Display
    case "EntityCard":
      return <A2UIEntityCard key={component.id} component={component} />;
    case "MemoryCard":
      return <A2UIMemoryCard key={component.id} component={component} />;

    // Specialized
    case "ExecutionTrace":
      return <A2UIExecutionTrace key={component.id} component={component} />;
    default:
      return (
        <div key={component.id} className="p-2 text-sm text-t-tertiary">
          [Unknown: {component.type}]
        </div>
      );
  }
}

export function A2UIRenderer({ surface, onAction }: RendererProps) {
  return (
    <div className="space-y-4">
      {(surface.children ?? []).map((child) => renderComponent(child, onAction, 0))}
    </div>
  );
}

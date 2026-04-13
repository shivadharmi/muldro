"use client";

import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";
import { ErrorBoundary } from "@/components/error-boundary";
import { A2UIAlert } from "./components/alert";
import { A2UIAvatar } from "./components/avatar";
import { A2UIBadge } from "./components/badge";
import { A2UIButton } from "./components/button";
import { A2UICalendar } from "./components/calendar";
import { A2UICard } from "./components/card";
import { A2UIChart } from "./components/chart";
import { A2UICodeBlock } from "./components/code-block";
import { A2UIColumn } from "./components/column";
import { A2UIDataGrid } from "./components/data-grid";
import { A2UIDivider } from "./components/divider";
import { A2UIEntityCard } from "./components/entity-card";
import { A2UIExecutionTrace } from "./components/execution-trace";
import { A2UIForm } from "./components/form";
import { A2UIKanbanBoard } from "./components/kanban-board";
import { A2UIList } from "./components/list";
import { A2UIMemoryCard } from "./components/memory-card";
import { A2UIMetric } from "./components/metric";
import { A2UIModal } from "./components/modal";
import { A2UIProgress } from "./components/progress";
import { A2UIRow } from "./components/row";
import { A2UISelect } from "./components/select";
import { A2UIStatusIndicator } from "./components/status-indicator";
import { A2UITable } from "./components/a2ui-table";
import { A2UITabs } from "./components/tabs";
import { A2UIText } from "./components/text";
import { A2UITextField } from "./components/text-field";
import { A2UITimeline } from "./components/timeline";
import { A2UIToggle } from "./components/toggle";

interface RendererProps {
  surface: A2UISurface;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

/** Maps A2UI component types to React implementations, wrapped in ErrorBoundary. */
function renderComponent(
  component: A2UIComponent,
  onAction: (action: string, payload: Record<string, unknown>) => void
): React.ReactNode {
  return (
    <ErrorBoundary
      key={`eb-${component.id}`}
      fallback={
        <div className="p-2 text-xs text-t-tertiary border border-yellow-500/30 rounded">
          Failed to render {component.type ?? "unknown"} component
        </div>
      }
    >
      {renderComponentInner(component, onAction)}
    </ErrorBoundary>
  );
}

/** Inner render dispatch — errors caught by the wrapping ErrorBoundary. */
function renderComponentInner(
  component: A2UIComponent,
  onAction: (action: string, payload: Record<string, unknown>) => void
): React.ReactNode {
  const children = component.children?.map((child) => renderComponent(child, onAction));

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
    case "Column":
      return <A2UIColumn key={component.id} component={component}>{children}</A2UIColumn>;
    case "List":
      return <A2UIList key={component.id} component={component}>{children}</A2UIList>;
    case "Divider":
      return <A2UIDivider key={component.id} component={component} />;
    case "Tabs":
      return (
        <A2UITabs
          key={component.id}
          component={component}
          renderChild={(child) => renderComponent(child, onAction)}
        >
          {children}
        </A2UITabs>
      );
    case "Modal":
      return <A2UIModal key={component.id} component={component}>{children}</A2UIModal>;
    case "Form":
      return <A2UIForm key={component.id} component={component}>{children}</A2UIForm>;

    // Input
    case "Button":
      return <A2UIButton key={component.id} component={component} onAction={onAction} />;
    case "TextField":
      return <A2UITextField key={component.id} component={component} onAction={onAction} />;
    case "Select":
      return <A2UISelect key={component.id} component={component} onAction={onAction} />;
    case "Toggle":
      return <A2UIToggle key={component.id} component={component} onAction={onAction} />;

    // Data
    case "Table":
      return <A2UITable key={component.id} component={component} />;
    case "DataGrid":
      return <A2UIDataGrid key={component.id} component={component} />;
    case "Timeline":
      return <A2UITimeline key={component.id} component={component} />;
    case "Metric":
      return <A2UIMetric key={component.id} component={component} />;
    case "Progress":
      return <A2UIProgress key={component.id} component={component} />;
    case "Chart":
      return <A2UIChart key={component.id} component={component} />;

    // Display
    case "Avatar":
      return <A2UIAvatar key={component.id} component={component} />;
    case "StatusIndicator":
      return <A2UIStatusIndicator key={component.id} component={component} />;
    case "EntityCard":
      return <A2UIEntityCard key={component.id} component={component} />;
    case "MemoryCard":
      return <A2UIMemoryCard key={component.id} component={component} />;

    // Specialized
    case "ExecutionTrace":
      return <A2UIExecutionTrace key={component.id} component={component} />;
    case "KanbanBoard":
      return <A2UIKanbanBoard key={component.id} component={component} />;
    case "Calendar":
      return <A2UICalendar key={component.id} component={component} />;
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
      {(surface.children ?? []).map((child) => renderComponent(child, onAction))}
    </div>
  );
}

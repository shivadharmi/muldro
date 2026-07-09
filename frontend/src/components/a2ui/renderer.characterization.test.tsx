/**
 * Step 9 P0 — characterization guardrails for the A2UIRenderer switch dispatch.
 *
 * These tests PIN the CURRENT (pre-cleanup) renderer behavior so Step 9's later
 * phases are fenced by green tests:
 *
 *   - P1 will DELETE the switch cases for the 13 never-produced component types
 *     (Chart, DataGrid, KanbanBoard, Calendar, Modal, Tabs, Form, TextField,
 *     Select, Toggle, Avatar, StatusIndicator, Column). After that, those types
 *     will fall through to the `[Unknown: …]` default. The Chart tripwire below
 *     encodes today's behavior (Chart renders its REAL component) so P1 has to
 *     flip it deliberately.
 *
 * Every assertion snapshots what renderer.tsx does right now — they PASS on write.
 */

import { render, screen } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { A2UIRenderer } from "./renderer";
import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";

function comp(partial: Partial<A2UIComponent> & { type: string; id: string }): A2UIComponent {
  return { properties: {}, children: [], actions: [], ...partial };
}

function surface(children: A2UIComponent[]): A2UISurface {
  return { type: "surface", id: "srf", children, metadata: {} };
}

// The 16 LIVE component types — the ones some backend builder actually produces.
// Each has a dedicated switch case in renderer.tsx today; none should fall through
// to the [Unknown] placeholder. Props are minimal; every LIVE component tolerates
// empty properties (|| "" / || [] fallbacks), so none hit the ErrorBoundary.
const LIVE_COMPONENTS: Array<Partial<A2UIComponent> & { type: string }> = [
  { type: "Text", properties: { text: "body copy" } },
  { type: "Badge", properties: { label: "new" } },
  { type: "Row", children: [comp({ type: "Text", id: "row-child", properties: { text: "in row" } })] },
  { type: "Card", children: [comp({ type: "Text", id: "card-child", properties: { text: "in card" } })] },
  { type: "Metric", properties: { label: "Relevance", value: 0.9 } },
  { type: "Button", properties: { label: "Run" } },
  { type: "Alert", properties: { message: "heads up" } },
  { type: "List", children: [comp({ type: "Text", id: "list-child", properties: { text: "item" } })] },
  { type: "Table", properties: { columns: [], rows: [] } },
  { type: "Timeline", properties: { events: [] } },
  { type: "MemoryCard", properties: { fact_text: "a fact", memory_type: "preference" } },
  { type: "Divider", properties: {} },
  { type: "CodeBlock", properties: { code: "x = 1" } },
  { type: "Progress", properties: { value: 50 } },
  { type: "EntityCard", properties: { name: "Acme", entity_type: "organization" } },
  { type: "ExecutionTrace", properties: { steps: [] } },
];

test.each(LIVE_COMPONENTS)(
  "live component $type renders without hitting the [Unknown] fallback",
  ({ type, properties, children }) => {
    render(
      <A2UIRenderer
        surface={surface([
          comp({ type, id: `node-${type}`, properties: properties ?? {}, children: children ?? [] }),
        ])}
        onAction={vi.fn()}
      />,
    );
    // A mapped type never renders the [Unknown: …] default — that's the whole
    // point of the characterization: its switch case exists.
    expect(screen.queryByText(new RegExp(`\\[Unknown: ${type}\\]`))).not.toBeInTheDocument();
  },
);

test("an unmapped component type DOES render the [Unknown] fallback", () => {
  render(
    <A2UIRenderer surface={surface([comp({ type: "Bogus", id: "x1" })])} onAction={vi.fn()} />,
  );
  expect(screen.getByText(/\[Unknown: Bogus\]/)).toBeInTheDocument();
});

test("TRIPWIRE: Chart is a dead type and renders the [Unknown] fallback", () => {
  // Chart was one of the 13 dead component types P1 deleted from the renderer
  // switch. Its case is gone, so a Chart node now falls through to the
  // `[Unknown: …]` default. This assertion was flipped in P1: it USED to assert
  // Chart rendered its real <A2UIChart/> component; it now pins the post-deletion
  // behavior (Chart -> [Unknown: Chart]).
  render(
    <A2UIRenderer
      surface={surface([
        comp({ type: "Chart", id: "chart-1", properties: { chart_type: "bar", data: { values: [1, 2, 3], labels: ["a", "b", "c"] } } }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText(/\[Unknown: Chart\]/)).toBeInTheDocument();
});

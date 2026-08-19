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

import { fireEvent, render, screen } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { A2UIRenderer } from "./renderer";
import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";

function comp(partial: Partial<A2UIComponent> & { type: string; id: string }): A2UIComponent {
  return { properties: {}, children: [], actions: [], ...partial };
}

function surface(children: A2UIComponent[]): A2UISurface {
  return { type: "surface", id: "srf", children, metadata: {} };
}

// The 17 LIVE component types — the ones some backend builder actually produces.
// Each has a dedicated switch case in renderer.tsx today; none should fall through
// to the [Unknown] placeholder. Props are minimal; every LIVE component tolerates
// empty properties (|| "" / || [] fallbacks), so none hit the ErrorBoundary.
const LIVE_COMPONENTS: Array<Partial<A2UIComponent> & { type: string }> = [
  { type: "Text", properties: { text: "body copy" } },
  { type: "Markdown", properties: { content: "# H\n- a\n- b" } },
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

test("Markdown renders its markdown content (heading + list), not the [Unknown] fallback", () => {
  // Step 9 P2: the Markdown component type is emitted by briefing/insight NARRATIVE
  // sections. It must render real markdown (react-markdown) — a heading and list —
  // and never fall through to the [Unknown: Markdown] placeholder.
  render(
    <A2UIRenderer
      surface={surface([
        comp({ type: "Markdown", id: "m1", properties: { content: "# H\n- a\n- b" } }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.queryByText(/\[Unknown: Markdown\]/)).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "H" })).toBeInTheDocument();
  expect(screen.getByText("a")).toBeInTheDocument();
  expect(screen.getByText("b")).toBeInTheDocument();
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

// ─── Table: positional cells (Task 5a) ───────────────────────────────────────
// The wire shape is now `rows: [{ cells: [...] }]`, positionally aligned to `columns`.
// The legacy keyed shape must keep rendering for surfaces persisted before the change,
// so BOTH are pinned here.

const TABLE_COLUMNS = [
  { key: "name", label: "Company" },
  { key: "raised", label: "Funding" },
];

test("Table renders positional cell rows", () => {
  render(
    <A2UIRenderer
      surface={surface([
        comp({
          type: "Table",
          id: "tbl-positional",
          properties: { columns: TABLE_COLUMNS, rows: [{ cells: ["Acme", "$10M"] }] },
        }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(screen.getByText("$10M")).toBeInTheDocument();
});

test("Table still renders a LEGACY keyed row (back-compat for persisted surfaces)", () => {
  render(
    <A2UIRenderer
      surface={surface([
        comp({
          type: "Table",
          id: "tbl-legacy",
          properties: { columns: TABLE_COLUMNS, rows: [{ name: "Acme", raised: "$10M" }] },
        }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(screen.getByText("$10M")).toBeInTheDocument();
});

test("a sortable Table actually REORDERS positional rows when a header is clicked", () => {
  // Regression guard: the sort comparator used to index rows by COLUMN KEY. Against the
  // positional shape every lookup returns undefined, so every comparison returns 0 and the
  // rows never move — a silent no-op with no error anywhere. The comparator must read the
  // cell positionally, exactly like the cell render does.
  render(
    <A2UIRenderer
      surface={surface([
        comp({
          type: "Table",
          id: "tbl-sort",
          properties: {
            columns: TABLE_COLUMNS,
            rows: [{ cells: ["Zeta", "$1M"] }, { cells: ["Acme", "$10M"] }],
            sortable: true,
          },
        }),
      ])}
      onAction={vi.fn()}
    />,
  );

  const firstCellText = () =>
    screen.getAllByRole("row")[1].querySelectorAll("td")[0].textContent;

  expect(firstCellText()).toBe("Zeta");
  fireEvent.click(screen.getByText("Company"));
  expect(firstCellText()).toBe("Acme");
});

// ─── Timeline: the closed event shape (Task 5b) ──────────────────────────────
// `TimelineProperties.events` was `list[dict]`, so the producer
// (`backend/src/ui/units.py::event_timeline`) emitted `timestamp`/`title`/`description`
// while this renderer read `time`/`title`/`source`. Only `title` matched: every run-events
// timeline drew a BLANK time line and silently dropped its description, and no test on
// either side could notice. These pin all three fields the closed `TimelineEvent` declares.

test("Timeline renders time, title AND description for an event", () => {
  render(
    <A2UIRenderer
      surface={surface([
        comp({
          type: "Timeline",
          id: "tl-full",
          properties: {
            events: [
              {
                time: "2026-08-20T09:00:00",
                title: "step_started",
                description: "Drafting the reply",
              },
            ],
          },
        }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText("2026-08-20T09:00:00")).toBeInTheDocument();
  expect(screen.getByText("step_started")).toBeInTheDocument();
  expect(screen.getByText("Drafting the reply")).toBeInTheDocument();
});

test("Timeline omits the optional lines when they are absent", () => {
  render(
    <A2UIRenderer
      surface={surface([
        comp({
          type: "Timeline",
          id: "tl-minimal",
          properties: { events: [{ time: "09:00", title: "approval_requested" }] },
        }),
      ])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText("approval_requested")).toBeInTheDocument();
  // Only the time and title lines — no empty supporting paragraphs.
  expect(document.querySelectorAll("p")).toHaveLength(2);
});

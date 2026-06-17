import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { A2UIRenderer } from "./renderer";
import type { A2UIComponent, A2UISurface } from "@/lib/a2ui-types";

function comp(partial: Partial<A2UIComponent> & { type: string; id: string }): A2UIComponent {
  return { properties: {}, children: [], actions: [], ...partial };
}

function surface(children: A2UIComponent[]): A2UISurface {
  return { type: "surface", id: "srf", children, metadata: {} };
}

test("dispatches a known leaf component type to its implementation", () => {
  render(
    <A2UIRenderer
      surface={surface([comp({ type: "Text", id: "t1", properties: { text: "hello world" } })])}
      onAction={vi.fn()}
    />,
  );
  expect(screen.getByText("hello world")).toBeInTheDocument();
});

test("recursively renders nested children (Card > Text)", () => {
  const tree = surface([
    comp({
      type: "Card",
      id: "c1",
      children: [comp({ type: "Text", id: "t1", properties: { text: "nested text" } })],
    }),
  ]);
  render(<A2UIRenderer surface={tree} onAction={vi.fn()} />);
  expect(screen.getByText("nested text")).toBeInTheDocument();
});

test("unknown component type falls through to the [Unknown] placeholder", () => {
  render(
    <A2UIRenderer surface={surface([comp({ type: "Frobnicator", id: "x1" })])} onAction={vi.fn()} />,
  );
  expect(screen.getByText(/\[Unknown: Frobnicator\]/)).toBeInTheDocument();
});

test("Button dispatches its first action through onAction", async () => {
  const onAction = vi.fn();
  const tree = surface([
    comp({
      type: "Button",
      id: "b1",
      properties: { label: "Run" },
      actions: [{ type: "click", payload: { foo: "bar" } }],
    }),
  ]);
  render(<A2UIRenderer surface={tree} onAction={onAction} />);
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(onAction).toHaveBeenCalledWith("click", { foo: "bar" });
});

test("a surface with no children renders without crashing", () => {
  const { container } = render(<A2UIRenderer surface={surface([])} onAction={vi.fn()} />);
  expect(container.firstChild).toBeInTheDocument();
  expect(container.textContent).toBe("");
});

test("renders a deeply-but-legally nested tree in full", () => {
  // 10 levels of Card nesting, well under the cap — the leaf must still render.
  let node = comp({ type: "Text", id: "leaf", properties: { text: "deep leaf" } });
  for (let i = 0; i < 10; i++) {
    node = comp({ type: "Card", id: `card-${i}`, children: [node] });
  }
  render(<A2UIRenderer surface={surface([node])} onAction={vi.fn()} />);
  expect(screen.getByText("deep leaf")).toBeInTheDocument();
});

test("caps pathologically deep trees with a truncation placeholder", () => {
  // 60 levels of nesting exceeds MAX_RENDER_DEPTH (24): the leaf must be truncated.
  let node = comp({ type: "Text", id: "leaf", properties: { text: "unreachable leaf" } });
  for (let i = 0; i < 60; i++) {
    node = comp({ type: "Card", id: `card-${i}`, children: [node] });
  }
  render(<A2UIRenderer surface={surface([node])} onAction={vi.fn()} />);
  expect(screen.queryByText("unreachable leaf")).not.toBeInTheDocument();
  expect(screen.getByText(/nested too deeply/i)).toBeInTheDocument();
});

test("missing children array on the surface is tolerated", () => {
  // Backend contract guarantees children[], but the renderer guards with `?? []`.
  const partial = { type: "surface", id: "srf", metadata: {} } as unknown as A2UISurface;
  const { container } = render(<A2UIRenderer surface={partial} onAction={vi.fn()} />);
  expect(container.firstChild).toBeInTheDocument();
});

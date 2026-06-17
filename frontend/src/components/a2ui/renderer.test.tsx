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

test("missing children array on the surface is tolerated", () => {
  // Backend contract guarantees children[], but the renderer guards with `?? []`.
  const partial = { type: "surface", id: "srf", metadata: {} } as unknown as A2UISurface;
  const { container } = render(<A2UIRenderer surface={partial} onAction={vi.fn()} />);
  expect(container.firstChild).toBeInTheDocument();
});

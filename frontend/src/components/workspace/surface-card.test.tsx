import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { SurfaceCard } from "./surface-card";
import type { WorkspaceSurface } from "@/stores/surface-store";
import type { A2UIComponent } from "@/lib/a2ui-types";

function preview(): WorkspaceSurface["preview"] {
  return {
    title: "Quarterly review",
    subtitle: null,
    status: null,
    priority: null,
    metrics: [],
    entities: [],
    progress: null,
    timestamp: null,
    tags: [],
  };
}

function surface(partial: Partial<WorkspaceSurface> = {}): WorkspaceSurface {
  return {
    id: "srf_1",
    kind: "summary",
    preview: preview(),
    detail_config: null,
    source_run_id: null,
    response_preview: null,
    created_at: "2026-06-17T00:00:00Z",
    ...partial,
  };
}

test("UI-P1-1: card root is a div with button role, not a real <button>", () => {
  const { container } = render(<SurfaceCard surface={surface()} onClick={vi.fn()} />);
  // A real <button> wrapping the body would nest interactive elements illegally.
  expect(container.querySelector("button")).toBeNull();
  const root = screen.getByRole("button", { name: /quarterly review/i });
  expect(root.tagName).toBe("DIV");
});

test("clicking the card opens it", async () => {
  const onClick = vi.fn();
  render(<SurfaceCard surface={surface()} onClick={onClick} />);
  await userEvent.click(screen.getByRole("button", { name: /quarterly review/i }));
  expect(onClick).toHaveBeenCalledTimes(1);
});

test("keyboard Enter/Space on the focused card activates it", async () => {
  const onClick = vi.fn();
  render(<SurfaceCard surface={surface()} onClick={onClick} />);
  const root = screen.getByRole("button", { name: /quarterly review/i });
  root.focus();
  await userEvent.keyboard("{Enter}");
  await userEvent.keyboard(" ");
  expect(onClick).toHaveBeenCalledTimes(2);
});

test("a nested interactive section renders without an illegal nested <button>", () => {
  // surface_data with a real Button — previously this lived inside a <button> root.
  const button: A2UIComponent = {
    type: "Button",
    id: "act",
    properties: { label: "Do it" },
    children: [],
    actions: [{ type: "click", payload: {} }],
  };
  const { container } = render(
    <SurfaceCard
      surface={surface({ surface_data: { sections: [button] } })}
      onClick={vi.fn()}
    />,
  );
  // The nested Button renders as a real <button>; the card root does not, so there is
  // exactly one <button> in the tree and no button-in-button nesting.
  const buttons = container.querySelectorAll("button");
  expect(buttons).toHaveLength(1);
  expect(buttons[0].textContent).toContain("Do it");
});

import { render, screen } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { WorkspaceCanvas } from "./workspace-canvas";
import type { Unit } from "@/lib/types/unit";

function unit(key: string, headline: string, importance: number): Unit {
  return {
    frame: {
      key,
      group_key: null,
      kind: "proposal",
      status: "needs_you",
      headline,
      source: "gmail",
      entity_type: "email_thread",
      occurred_at: "2026-08-21T14:14:00Z",
      updated_at: "2026-08-21T14:14:00Z",
      importance,
      event_count: 1,
      affordances: [],
    },
    body: "A short lede.",
    quotes: [],
  };
}

test("renders one card per unit", () => {
  render(
    <WorkspaceCanvas
      units={[unit("a", "First thing", 0.9), unit("b", "Second thing", 0.5)]}
      onOpen={vi.fn()}
    />,
  );
  expect(screen.getByText("First thing")).toBeInTheDocument();
  expect(screen.getByText("Second thing")).toBeInTheDocument();
});

test("renders the empty state when there are no units", () => {
  render(<WorkspaceCanvas units={[]} onOpen={vi.fn()} />);
  expect(screen.getByText(/Nothing needs your attention/)).toBeInTheDocument();
});

test("does not use dense grid packing", () => {
  const { container } = render(
    <WorkspaceCanvas units={[unit("a", "First thing", 0.9)]} onOpen={vi.fn()} />,
  );
  const grid = container.querySelector<HTMLElement>("[data-testid='unit-grid']");
  expect(grid?.style.gridAutoFlow).not.toBe("dense");
});

/**
 * The ranker orders and never cuts, so nothing rendered the order AS an order:
 * the client drew every element of a carefully sequenced list. The fold is
 * where attention stops — and it FOLDS rather than filters, because a hidden
 * thing that cannot be reached is a lie about coverage.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

vi.mock("@/components/error-boundary", () => ({
  ErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/workspace/unit-card", () => ({
  UnitCard: ({ unit }: { unit: { frame: { key: string; headline: string } } }) => (
    <div data-testid="card">{unit.frame.headline}</div>
  ),
}));

import { WorkspaceCanvas } from "./workspace-canvas";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const u = (key: string): any => ({
  frame: { key, headline: `card ${key}`, kind: "finding", status: "new", source: "gmail" },
  body: "",
  quotes: [],
});

const noop = () => {};

test("only the head is rendered, and the tail is one row", () => {
  render(<WorkspaceCanvas units={[u("a"), u("b"), u("c")]} onOpen={noop} foldAfter={1} />);
  expect(screen.getAllByTestId("card")).toHaveLength(1);
  expect(screen.getByText(/2 quieter items/)).toBeTruthy();
});

test("the tail is reachable, not gone", async () => {
  render(<WorkspaceCanvas units={[u("a"), u("b"), u("c")]} onOpen={noop} foldAfter={1} />);
  await userEvent.click(screen.getByRole("button", { name: /2 quieter items/ }));
  expect(screen.getAllByTestId("card")).toHaveLength(3);
});

test("nothing folded means no row at all", () => {
  render(<WorkspaceCanvas units={[u("a"), u("b")]} onOpen={noop} foldAfter={2} />);
  expect(screen.getAllByTestId("card")).toHaveLength(2);
  expect(screen.queryByText(/quieter/)).toBeNull();
});

test("an all-quiet feed collapses to the row alone", () => {
  render(<WorkspaceCanvas units={[u("a"), u("b")]} onOpen={noop} foldAfter={0} />);
  expect(screen.queryAllByTestId("card")).toHaveLength(0);
  expect(screen.getByText(/2 quieter items/)).toBeTruthy();
});

test("one folded item is singular", () => {
  render(<WorkspaceCanvas units={[u("a"), u("b")]} onOpen={noop} foldAfter={1} />);
  expect(screen.getByText(/1 quieter item$/)).toBeTruthy();
});

test("a caller that knows nothing about the fold shows everything", () => {
  render(<WorkspaceCanvas units={[u("a"), u("b"), u("c")]} onOpen={noop} />);
  expect(screen.getAllByTestId("card")).toHaveLength(3);
});

test.each([-1, 99, NaN])("a bad index from the wire costs the fold, not the feed (%s)", (bad) => {
  render(<WorkspaceCanvas units={[u("a"), u("b")]} onOpen={noop} foldAfter={bad} />);
  expect(screen.getAllByTestId("card")).toHaveLength(2);
  expect(screen.queryByText(/quieter/)).toBeNull();
});

test("the empty state still wins over the fold", () => {
  render(<WorkspaceCanvas units={[]} onOpen={noop} foldAfter={0} />);
  expect(screen.getByText(/Nothing needs your attention/)).toBeTruthy();
});

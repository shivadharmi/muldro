import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { Lede } from "./lede";

test("renders emphasis", () => {
  render(<Lede text="muldrov1 is **where everything is happening**" />);
  expect(screen.getByText("where everything is happening").tagName).toBe("STRONG");
});

test("renders code spans", () => {
  render(<Lede text="the PR on `rules` is stale" />);
  expect(screen.getByText("rules").tagName).toBe("CODE");
});

test("renders a link as plain text, never as an anchor", () => {
  const { container } = render(<Lede text="[Verify](https://phish.example)" />);
  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toContain("Verify");
});

test("does not autolink a bare URL", () => {
  const { container } = render(<Lede text="see https://phish.example now" />);
  expect(container.querySelector("a")).toBeNull();
});

test("does not autolink an angle-bracket URL", () => {
  const { container } = render(<Lede text="see <https://phish.example> now" />);
  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toContain("phish.example");
});

test("renders a heading as inline text, not a heading element", () => {
  const { container } = render(<Lede text="# Repository activity" />);
  expect(container.querySelector("h1")).toBeNull();
  expect(container.textContent).toContain("Repository activity");
});

test("renders a list as inline text, not a list element", () => {
  const { container } = render(<Lede text="- one\n- two" />);
  expect(container.querySelector("ul")).toBeNull();
  expect(container.querySelector("li")).toBeNull();
});

test("renders nothing for an empty lede", () => {
  const { container } = render(<Lede text="" />);
  expect(container.textContent).toBe("");
});

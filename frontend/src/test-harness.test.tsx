import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";

test("test harness renders React and jest-dom matchers work", () => {
  render(<span>jarvis-harness-ok</span>);
  expect(screen.getByText("jarvis-harness-ok")).toBeInTheDocument();
});

import { test, expect } from "vitest";
import { resolveFirstRunState } from "./first-run-state";

test("no sources and no briefing -> onboarding", () => {
  expect(resolveFirstRunState(0, false)).toBe("onboarding");
});

test("sources connected but no briefing -> gathering", () => {
  expect(resolveFirstRunState(2, false)).toBe("gathering");
});

test("briefing present -> active", () => {
  expect(resolveFirstRunState(2, true)).toBe("active");
});

test("briefing present with zero sources -> active (precedence)", () => {
  expect(resolveFirstRunState(0, true)).toBe("active");
});

test("anything on screen means the workspace is live, not gathering", () => {
  // The reported bug: five finding cards rendered UNDER a card announcing that
  // there was nothing to show yet. An empty state is a claim about emptiness.
  expect(resolveFirstRunState(2, false, true)).toBe("active");
});

test("no units and no briefing is still gathering", () => {
  expect(resolveFirstRunState(2, false, false)).toBe("gathering");
});

test("units cannot rescue a workspace with no sources", () => {
  // Onboarding outranks content: with nothing connected, the one thing the
  // founder needs is the connect step, whatever else happens to be on screen.
  expect(resolveFirstRunState(0, false, true)).toBe("onboarding");
});

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

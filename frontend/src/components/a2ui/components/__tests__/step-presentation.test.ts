import { test, expect } from "vitest";
import { stepStatusIcon } from "../step-presentation";
import { isStepDone } from "@/lib/a2ui-types";

test("completed_unverified renders the sent-but-unconfirmed icon", () => {
  expect(stepStatusIcon("completed_unverified").icon).toBe("✓?");
});

test("partially_completed renders the read-back-contradicted icon", () => {
  expect(stepStatusIcon("partially_completed").icon).toBe("⚠");
});

test("completed renders a plain check", () => {
  expect(stepStatusIcon("completed").icon).toBe("✓");
});

test("isStepDone: completed_unverified is done, partially_completed is not", () => {
  expect(isStepDone("completed_unverified")).toBe(true);
  expect(isStepDone("partially_completed")).toBe(false);
  expect(isStepDone("completed")).toBe(true);
});

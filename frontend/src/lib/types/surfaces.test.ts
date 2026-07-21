import { test, expect, vi, afterEach } from "vitest";
import { normalizeSurfaceKind } from "./surfaces";

afterEach(() => {
  vi.restoreAllMocks();
});

test("known kind passes through unchanged", () => {
  expect(normalizeSurfaceKind("briefing", "srf_1")).toBe("briefing");
  expect(normalizeSurfaceKind("run", "srf_1")).toBe("run");
});

test("removed legacy kinds now degrade to summary with a warning", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  for (const k of ["checklist", "comparison", "timeline", "table", "activity"]) {
    expect(normalizeSurfaceKind(k, "srf_1")).toBe("summary");
  }
  expect(warn).toHaveBeenCalled();
});

test("missing/empty kind defaults to summary without warning", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  expect(normalizeSurfaceKind(null, "srf_1")).toBe("summary");
  expect(normalizeSurfaceKind(undefined, "srf_1")).toBe("summary");
  expect(normalizeSurfaceKind("", "srf_1")).toBe("summary");
  expect(warn).not.toHaveBeenCalled();
});

test("unknown non-empty kind defaults to summary AND warns (contract drift)", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  expect(normalizeSurfaceKind("totally_new_kind", "srf_42")).toBe("summary");
  expect(warn).toHaveBeenCalledOnce();
  expect(warn.mock.calls[0][0]).toContain("totally_new_kind");
  expect(warn.mock.calls[0][0]).toContain("srf_42");
});

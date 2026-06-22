import { test, expect } from "vitest";
import { sortSurfacesActiveFirst } from "./surface-merge";
import type { WorkspaceSurface } from "@/stores/surface-store";

function srf(partial: Partial<WorkspaceSurface> & { id: string }): WorkspaceSurface {
  return {
    kind: "summary",
    preview: {} as WorkspaceSurface["preview"],
    detail_config: null,
    source_run_id: null,
    response_preview: null,
    created_at: "2026-06-01T00:00:00Z",
    ...partial,
  };
}

test("active surfaces (live phase or insight) sort before inactive ones", () => {
  const inactive = srf({ id: "a", created_at: "2026-06-10T00:00:00Z" });
  const executing = srf({ id: "b", phase: "executing", created_at: "2026-06-01T00:00:00Z" });
  const insight = srf({ id: "c", kind: "proactive_insight", created_at: "2026-06-02T00:00:00Z" });

  const sorted = sortSurfacesActiveFirst([inactive, executing, insight]);
  // Both active ones come first (despite older timestamps), inactive last.
  expect(sorted.map((s) => s.id).slice(0, 2).sort()).toEqual(["b", "c"]);
  expect(sorted[2].id).toBe("a");
});

test("within the same active-ness, newer created_at wins", () => {
  const older = srf({ id: "old", created_at: "2026-06-01T00:00:00Z" });
  const newer = srf({ id: "new", created_at: "2026-06-09T00:00:00Z" });
  expect(sortSurfacesActiveFirst([older, newer]).map((s) => s.id)).toEqual(["new", "old"]);
});

test("equal created_at falls back to a deterministic id tie-break", () => {
  const x = srf({ id: "x", created_at: "2026-06-05T00:00:00Z" });
  const y = srf({ id: "y", created_at: "2026-06-05T00:00:00Z" });
  // Stable regardless of input order.
  expect(sortSurfacesActiveFirst([y, x]).map((s) => s.id)).toEqual(["x", "y"]);
  expect(sortSurfacesActiveFirst([x, y]).map((s) => s.id)).toEqual(["x", "y"]);
});

test("does not mutate the input array", () => {
  const input = [srf({ id: "a" }), srf({ id: "b", phase: "executing" })];
  const snapshot = input.map((s) => s.id);
  sortSurfacesActiveFirst(input);
  expect(input.map((s) => s.id)).toEqual(snapshot);
});

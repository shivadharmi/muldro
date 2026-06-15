import { test, expect, beforeEach } from "vitest";
import { useSurfaceStore, type WorkspaceSurface } from "./surface-store";
import type { StepState, SurfaceUpdate } from "@/lib/a2ui-types";

function baseSurface(): WorkspaceSurface {
  return {
    id: "srf_1",
    kind: "run",
    preview: {} as WorkspaceSurface["preview"],
    detail_config: null,
    source_run_id: null,
    response_preview: null,
    created_at: "2026-06-15T00:00:00Z",
    steps: [{ id: "s1", label: "do thing", status: "running" } as unknown as StepState],
  };
}

function update(partial: Partial<SurfaceUpdate>): SurfaceUpdate {
  return {
    surface_id: "srf_1",
    phase: "executing",
    steps: [],
    current_step: null,
    progress: "",
    approval: null,
    results: null,
    ...partial,
  } as SurfaceUpdate;
}

beforeEach(() => {
  useSurfaceStore.getState().setSurfaces([]);
});

test("updateSurface can clear steps with an empty array (UI-P2-2)", () => {
  const store = useSurfaceStore.getState();
  store.addSurface(baseSurface());
  expect(useSurfaceStore.getState().surfaces[0].steps).toHaveLength(1);

  store.updateSurface("srf_1", update({ steps: [] }));

  expect(useSurfaceStore.getState().surfaces[0].steps).toEqual([]);
});

test("updateSurface still applies a non-empty steps array", () => {
  const store = useSurfaceStore.getState();
  store.addSurface(baseSurface());

  const newSteps = [
    { id: "s1", label: "a", status: "completed" },
    { id: "s2", label: "b", status: "running" },
  ] as unknown as StepState[];
  store.updateSurface("srf_1", update({ steps: newSteps }));

  expect(useSurfaceStore.getState().surfaces[0].steps).toHaveLength(2);
});

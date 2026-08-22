import { test, expect, beforeEach } from "vitest";
import { useSurfaceStore, type WorkspaceSurface } from "./surface-store";
import type { StepState, SurfaceUpdate } from "@/lib/types/execution";

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

test("updateSurface merges phase/progress/current_step selectively", () => {
  const store = useSurfaceStore.getState();
  store.addSurface(baseSurface());

  store.updateSurface(
    "srf_1",
    update({ phase: "completed", progress: "done", current_step: "s2" }),
  );

  const merged = useSurfaceStore.getState().surfaces[0];
  expect(merged.phase).toBe("completed");
  expect(merged.progress).toBe("done");
  expect(merged.current_step).toBe("s2");
  // Untouched identity fields survive the partial merge.
  expect(merged.id).toBe("srf_1");
  expect(merged.kind).toBe("run");
});

test("updateSurface applies approval and results payloads", () => {
  const store = useSurfaceStore.getState();
  store.addSurface(baseSurface());

  const approval = { approval_id: "apr_1" } as unknown as SurfaceUpdate["approval"];
  const results = { key_findings: ["x"] } as unknown as SurfaceUpdate["results"];
  store.updateSurface("srf_1", update({ phase: "approval_needed", approval, results }));

  const merged = useSurfaceStore.getState().surfaces[0];
  expect(merged.approval).toEqual(approval);
  expect(merged.results).toEqual(results);
});

test("updateSurface leaves a field untouched when the update omits it (undefined)", () => {
  const store = useSurfaceStore.getState();
  store.addSurface({ ...baseSurface(), progress: "original" });

  // An update object whose `progress` is undefined must not clobber the existing value.
  const partial = {
    surface_id: "srf_1",
    phase: "executing",
    steps: undefined,
    current_step: undefined,
    progress: undefined,
    approval: undefined,
    results: undefined,
  } as unknown as SurfaceUpdate;
  store.updateSurface("srf_1", partial);

  const merged = useSurfaceStore.getState().surfaces[0];
  expect(merged.phase).toBe("executing");
  expect(merged.progress).toBe("original");
});

test("updateSurface is a no-op for an unknown surface id", () => {
  const store = useSurfaceStore.getState();
  store.addSurface(baseSurface());
  store.updateSurface("srf_missing", update({ phase: "completed" }));

  expect(useSurfaceStore.getState().surfaces[0].phase).toBeUndefined();
});

test("closeDetailModal drops the active surface so the modal unmounts with its tab cache", () => {
  const store = useSurfaceStore.getState();
  store.openDetailModal("prepared_work_ws_test");
  expect(useSurfaceStore.getState().activeSurfaceId).toBe("prepared_work_ws_test");

  store.closeDetailModal();

  // Both must clear: `prepared_work_{workspace_id}` is a standing singleton, so a surviving
  // activeSurfaceId keeps the modal mounted and its fetched detail cached across opens.
  expect(useSurfaceStore.getState().detailModalOpen).toBe(false);
  expect(useSurfaceStore.getState().activeSurfaceId).toBeNull();
});

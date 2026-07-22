import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { test, expect, vi, beforeEach } from "vitest";
import { SurfaceDetailModal } from "./surface-detail-modal";
import type { WorkspaceSurface } from "@/stores/surface-store";
import { fetchSurfaceDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchSurfaceDetail: vi.fn().mockResolvedValue({
    tab_id: "steps",
    sections: [{ id: "s1", title: "TAB_STEPS_SECTION", collapsed: false, children: [] }],
  }),
}));

function runSurface(): WorkspaceSurface {
  return {
    id: "run_01",
    kind: "run",
    preview: {
      title: "Live run goal",
      subtitle: null, status: "running", priority: null, metrics: [],
      entities: [], progress: null, timestamp: null, tags: [],
    },
    detail_config: { tabs: [{ id: "steps", label: "Steps", endpoint: "/x" }], default_tab: "steps" },
    phase: "executing",
    steps: [], current_step: null, progress: "", approval: null, results: null,
  } as unknown as WorkspaceSurface;
}

function renderModal(surface: WorkspaceSurface) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <SurfaceDetailModal surface={surface} open={true} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.mocked(fetchSurfaceDetail).mockClear());

test("when phase is set, live exec surface renders and detail tabs do NOT", () => {
  renderModal(runSurface());
  expect(screen.getAllByText("Live run goal").length).toBeGreaterThan(0);
  expect(screen.queryByText("TAB_STEPS_SECTION")).toBeNull();
  expect(fetchSurfaceDetail).not.toHaveBeenCalled();
});

test("when phase is absent, detail tabs render and fetch", async () => {
  const s = runSurface();
  const noPhase = { ...s, phase: null } as unknown as WorkspaceSurface;
  renderModal(noPhase);
  expect(await screen.findByText("TAB_STEPS_SECTION")).toBeTruthy();
  expect(fetchSurfaceDetail).toHaveBeenCalledWith("run_01", "steps");
});

test("a live phase transition on the SAME surface id clears the stale cached tab", async () => {
  const s = runSurface();
  const noPhase = { ...s, phase: null } as unknown as WorkspaceSurface;
  const qc = new QueryClient();
  const { rerender } = render(
    <QueryClientProvider client={qc}>
      <SurfaceDetailModal surface={noPhase} open={true} onClose={() => {}} />
    </QueryClientProvider>,
  );

  // Tab fetch resolves and caches the "steps" tab content.
  expect(await screen.findByText("TAB_STEPS_SECTION")).toBeTruthy();

  // Same surface id, but a WS update now sets phase — modal stays open,
  // no prop-identity change to trigger the render-phase reset.
  const nowExecuting = { ...noPhase, phase: "executing" } as unknown as WorkspaceSurface;
  rerender(
    <QueryClientProvider client={qc}>
      <SurfaceDetailModal surface={nowExecuting} open={true} onClose={() => {}} />
    </QueryClientProvider>,
  );

  expect(screen.queryByText("TAB_STEPS_SECTION")).toBeNull();
  expect(screen.getAllByText("Live run goal").length).toBeGreaterThan(0);
});

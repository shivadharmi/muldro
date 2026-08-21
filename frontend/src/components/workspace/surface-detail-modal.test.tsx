import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { test, expect, vi, beforeEach } from "vitest";
import { SurfaceDetailModal } from "./surface-detail-modal";
import { useSurfaceStore, type WorkspaceSurface } from "@/stores/surface-store";
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

// The prepared-work queue is a STANDING singleton — `prepared_work_{workspace_id}` never
// changes id as its rows come and go, unlike every other detail surface, which is per-record
// and transient. So the render-phase reset (keyed on surface.id) never fires for it, and the
// tab cache has to be dropped some other way: closing clears activeSurfaceId, which unmounts
// the modal. Without that the founder approves item 1, reopens, and sees it still listed
// with a live Approve button.
function queueSurface(): WorkspaceSurface {
  return {
    id: "prepared_work_ws_test",
    kind: "prepared_work",
    preview: {
      title: "Prepared for your review",
      subtitle: null, status: "awaiting_approval", priority: null, metrics: [],
      entities: [], progress: null, timestamp: null, tags: [],
    },
    detail_config: { tabs: [{ id: "steps", label: "Queue", endpoint: "/x" }], default_tab: "steps" },
    phase: null,
  } as unknown as WorkspaceSurface;
}

/** Mirrors the page's mount condition: the modal exists only while a surface is active. */
function StoreDrivenModal() {
  const activeSurfaceId = useSurfaceStore((s) => s.activeSurfaceId);
  const detailModalOpen = useSurfaceStore((s) => s.detailModalOpen);
  const closeDetailModal = useSurfaceStore((s) => s.closeDetailModal);
  if (!activeSurfaceId) return null;
  return (
    <SurfaceDetailModal surface={queueSurface()} open={detailModalOpen} onClose={closeDetailModal} />
  );
}

test("reopening a standing surface refetches its tab rather than replaying the stale cache", async () => {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <StoreDrivenModal />
    </QueryClientProvider>,
  );

  act(() => useSurfaceStore.getState().openDetailModal("prepared_work_ws_test"));
  expect(await screen.findByText("TAB_STEPS_SECTION")).toBeTruthy();
  expect(fetchSurfaceDetail).toHaveBeenCalledTimes(1);

  // Approving a queued item closes the modal; the row it showed is now decided.
  act(() => useSurfaceStore.getState().closeDetailModal());
  expect(screen.queryByText("TAB_STEPS_SECTION")).toBeNull();

  act(() => useSurfaceStore.getState().openDetailModal("prepared_work_ws_test"));
  expect(await screen.findByText("TAB_STEPS_SECTION")).toBeTruthy();
  expect(fetchSurfaceDetail).toHaveBeenCalledTimes(2);
});

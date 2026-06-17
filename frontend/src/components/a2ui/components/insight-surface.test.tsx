import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, beforeEach, vi, type Mock } from "vitest";
import { InsightSurface } from "./insight-surface";
import { useWsActionStore } from "@/stores/ws-action-store";
import { useSurfaceStore, type WorkspaceSurface } from "@/stores/surface-store";
import { dismissInsight } from "@/lib/api";
import type { InsightData, SuggestedActionRef } from "@/lib/a2ui-types";

vi.mock("@/lib/api", () => ({
  dismissInsight: vi.fn().mockResolvedValue(undefined),
}));

function action(partial: Partial<SuggestedActionRef> = {}): SuggestedActionRef {
  return {
    description: "Draft a reply",
    capability: "email.draft",
    action_input: {},
    action_preview: "Draft a reply to the thread",
    ...partial,
  };
}

function insight(partial: Partial<InsightData> = {}): InsightData {
  return {
    signal_source: "gmail",
    signal_category: "email",
    signal_summary: "New investor email needs a reply",
    relevance_score: 0.9,
    relevance_reasoning: "Matches your fundraising goal",
    related_goals: ["Close the seed round"],
    suggested_actions: [action()],
    dismiss_available: true,
    ...partial,
  };
}

let sendAction: Mock<(action: string, payload: Record<string, unknown>) => void>;

beforeEach(() => {
  sendAction = vi.fn();
  useWsActionStore.getState().setSendAction(sendAction);
  useSurfaceStore.getState().setSurfaces([]);
  vi.mocked(dismissInsight).mockClear();
});

test("clicking a suggested action dispatches execute_insight with its index", async () => {
  const data = insight({
    suggested_actions: [action({ description: "First" }), action({ description: "Second" })],
  });
  render(<InsightSurface surfaceId="srf_x" insightData={data} />);

  await userEvent.click(screen.getByRole("button", { name: "Second" }));
  expect(sendAction).toHaveBeenCalledWith("execute_insight", {
    surface_id: "srf_x",
    action_index: 1,
  });
});

test("high relevance score surfaces the 'High relevance' marker", () => {
  render(<InsightSurface surfaceId="srf_x" insightData={insight({ relevance_score: 0.8 })} />);
  expect(screen.getByText(/high relevance/i)).toBeInTheDocument();
});

test("low relevance score hides the 'High relevance' marker", () => {
  render(<InsightSurface surfaceId="srf_x" insightData={insight({ relevance_score: 0.4 })} />);
  expect(screen.queryByText(/high relevance/i)).not.toBeInTheDocument();
});

test("dismiss confirmation calls the API and removes the surface from the store", async () => {
  useSurfaceStore.getState().addSurface({
    id: "srf_x",
    kind: "proactive_insight",
  } as unknown as WorkspaceSurface);

  render(<InsightSurface surfaceId="srf_x" insightData={insight()} />);

  await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  await userEvent.click(screen.getByRole("button", { name: "Yes, Dismiss" }));

  await waitFor(() => expect(dismissInsight).toHaveBeenCalledWith("srf_x"));
  expect(useSurfaceStore.getState().surfaces.find((s) => s.id === "srf_x")).toBeUndefined();
});

test("hidden dismiss control when dismissal is unavailable", () => {
  render(
    <InsightSurface surfaceId="srf_x" insightData={insight({ dismiss_available: false })} />,
  );
  expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
});

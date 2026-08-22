import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { RunRow } from "./run-row";
import type { HistoryItem, HistoryApprovalContext } from "@/stores/history-store";
import type { ApprovalContext } from "@/lib/types/execution";

function richApproval(partial: Partial<ApprovalContext> = {}): ApprovalContext {
  return {
    approval_id: "apr_1",
    step_description: "Send the launch email",
    risk_level: "high",
    trust_level: "learning",
    expires_at: null,
    triggering_step_id: "step_2",
    graduation_hint: "3 more approvals to auto-run",
    risk_reasoning: "Sends an external email to investors",
    trust_context: "Similar to 2 prior approvals",
    reversible: false,
    blast_radius: "external",
    effective_trust_level: "learning",
    approved_count: 2,
    rejected_count: 0,
    ...partial,
  };
}

function thinApproval(): HistoryApprovalContext {
  return {
    approval_id: "apr_1",
    step_id: "step_2",
    step_description: "Send the launch email",
    risk_level: "high",
    trust_level: null,
  };
}

function makeItem(approval: HistoryItem["approval"]): HistoryItem {
  return {
    run_id: "run_1",
    plan_id: "plan_1",
    goal: "Email the investors",
    source: "chat",
    trigger_type: "user",
    status: "awaiting_approval",
    risk_level: "high",
    started_at: new Date().toISOString(),
    completed_at: null,
    error: null,
    retry_count: 0,
    step_count: 2,
    completed_step_count: 1,
    cost_usd: null,
    steps: [],
    approval,
    live_phase: "approval_needed",
    surface_id: "run_1",
  };
}

// The unified InlineApprovalCard is distinguishable from the legacy thin
// RunApprovalCard by its "Edit" affordance and its collapsible evidence summary.
test("RunRow renders the unified InlineApprovalCard from a REST-sourced rich ApprovalContext", () => {
  render(<RunRow item={makeItem(richApproval())} />);

  // Rich evidence only the InlineApprovalCard surfaces:
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByText("Why does this need approval?")).toBeInTheDocument();
  expect(screen.getByText("Sends an external email to investors")).toBeInTheDocument();
  expect(screen.getByText("3 more approvals to auto-run")).toBeInTheDocument();
});

test("RunRow does NOT render the InlineApprovalCard for a thin approval (fallback)", () => {
  render(<RunRow item={makeItem(thinApproval())} />);
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(screen.queryByText("Why does this need approval?")).not.toBeInTheDocument();
});

test("RunRow does NOT render the InlineApprovalCard when there is no approval", () => {
  render(<RunRow item={makeItem(null)} />);
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { InlineApprovalCard } from "../inline-approval";
import type { ApprovalContext } from "@/lib/a2ui-types";

// B12 / P3.2: ONE InlineApprovalCard, fed by the SAME rich `ApprovalContext` from
// BOTH the live-WS path (trust_gate → SurfaceUpdate.approval) AND the persisted/REST
// path (routes_history enrichment from last_surface_update.approval). Both shapes are
// the same unified type; this locks that the card renders the rich evidence from each.

// As the live-WS trust_gate emits it (SurfaceUpdate.approval).
const wsShaped: ApprovalContext = {
  approval_id: "apr_ws",
  step_description: "Create the calendar invite",
  risk_level: "medium",
  trust_level: "trusted",
  expires_at: null,
  triggering_step_id: "step_1",
  graduation_hint: "1 more to auto-run",
  risk_reasoning: "Writes to your primary calendar",
  trust_context: "Similar to 9 prior approvals",
  reversible: true,
  blast_radius: "self",
  effective_trust_level: "trusted",
  approved_count: 9,
  rejected_count: 0,
};

// As the REST enrichment reconstructs it from the persisted surface.
const restShaped: ApprovalContext = {
  approval_id: "apr_rest",
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
  rejected_count: 1,
};

test("InlineApprovalCard renders rich evidence from a WS-shaped ApprovalContext", () => {
  render(<InlineApprovalCard approval={wsShaped} />);
  expect(screen.getByText("Create the calendar invite")).toBeInTheDocument();
  expect(screen.getByText("Writes to your primary calendar")).toBeInTheDocument();
  expect(screen.getByText("1 more to auto-run")).toBeInTheDocument();
  expect(screen.getByText("Approved: 9")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("InlineApprovalCard renders rich evidence from a REST-shaped ApprovalContext", () => {
  render(<InlineApprovalCard approval={restShaped} />);
  expect(screen.getByText("Send the launch email")).toBeInTheDocument();
  expect(screen.getByText("Sends an external email to investors")).toBeInTheDocument();
  expect(screen.getByText("3 more approvals to auto-run")).toBeInTheDocument();
  expect(screen.getByText("Approved: 2")).toBeInTheDocument();
  expect(screen.getByText("Rejected: 1")).toBeInTheDocument();
  // Irreversible badge only shows for reversible=false.
  expect(screen.getByText("Irreversible")).toBeInTheDocument();
});

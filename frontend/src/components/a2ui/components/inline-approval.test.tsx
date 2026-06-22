import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, beforeEach, vi, type Mock } from "vitest";
import { InlineApprovalCard } from "./inline-approval";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { ApprovalContext } from "@/lib/a2ui-types";

function approval(partial: Partial<ApprovalContext> = {}): ApprovalContext {
  return {
    approval_id: "apr_1",
    step_description: "Send the launch email",
    risk_level: "high",
    trust_level: "learning",
    expires_at: null,
    triggering_step_id: null,
    graduation_hint: "",
    risk_reasoning: "Sends an external email",
    trust_context: "",
    reversible: false,
    blast_radius: "self",
    effective_trust_level: "learning",
    approved_count: 0,
    rejected_count: 0,
    ...partial,
  };
}

let sendAction: Mock<(action: string, payload: Record<string, unknown>) => void>;

beforeEach(() => {
  sendAction = vi.fn();
  useWsActionStore.getState().setSendAction(sendAction);
});

test("Approve dispatches the approve action with the approval id", async () => {
  render(<InlineApprovalCard approval={approval()} />);
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(sendAction).toHaveBeenCalledWith("approve", { id: "apr_1" });
});

test("Edit dispatches edit_before_approve", async () => {
  render(<InlineApprovalCard approval={approval()} />);
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(sendAction).toHaveBeenCalledWith("edit_before_approve", { id: "apr_1" });
});

test("Reject opens a confirmation and dispatches reject with the typed reason", async () => {
  render(<InlineApprovalCard approval={approval()} />);
  // The inline Reject button only opens the modal — it must not dispatch directly.
  await userEvent.click(screen.getByRole("button", { name: "Reject" }));
  expect(sendAction).not.toHaveBeenCalled();

  await userEvent.type(screen.getByPlaceholderText(/wrong recipients/i), "not now");
  await userEvent.click(screen.getByRole("button", { name: "Yes, Reject" }));
  expect(sendAction).toHaveBeenCalledWith("reject", { id: "apr_1", reason: "not now" });
});

test("an expired approval disables the action buttons and blocks dispatch", async () => {
  const expired = approval({ expires_at: new Date(Date.now() - 60_000).toISOString() });
  render(<InlineApprovalCard approval={expired} />);

  const approveBtn = screen.getByRole("button", { name: "Approve" });
  expect(approveBtn).toBeDisabled();
  expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  expect(screen.getByText("Expired")).toBeInTheDocument();

  await userEvent.click(approveBtn);
  expect(sendAction).not.toHaveBeenCalled();
});

test("a future expiry shows a live countdown, not 'Expired'", () => {
  const future = approval({ expires_at: new Date(Date.now() + 90_000).toISOString() });
  render(<InlineApprovalCard approval={future} />);
  expect(screen.queryByText("Expired")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve" })).not.toBeDisabled();
});

/**
 * The only place a prepared action can be acted on.
 *
 * Prepared work is a write that was STAGED, not executed, because a gate
 * wanted a human and none was present. Nothing has run. This list is the
 * review, and its buttons are the decision.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PreparedQueue } from "./prepared-queue";

const fetchApprovals = vi.fn();
const approveAction = vi.fn();
const rejectAction = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchApprovals: (...a: unknown[]) => fetchApprovals(...a),
  approveAction: (...a: unknown[]) => approveAction(...a),
  rejectAction: (...a: unknown[]) => rejectAction(...a),
}));

const ROW = {
  approval_id: "apr_1",
  status: "pending",
  title: "Send the term sheet reply to Sarah Chen",
  summary: "One outbound email.",
  approval_type: "prepared_action",
  risk_level: "high",
  created_at: "2026-08-22T10:00:00Z",
};

describe("PreparedQueue", () => {
  beforeEach(() => {
    fetchApprovals.mockReset().mockResolvedValue([ROW]);
    approveAction.mockReset().mockResolvedValue({});
    rejectAction.mockReset().mockResolvedValue({});
  });

  it("asks for everything pending, not one type", async () => {
    /* It asked for `prepared_action` alone while five types existed, so a
       filter proposal, a step approval and the Governor's plan-level rows were
       written and rendered nowhere. The old test pinned the exact arguments,
       so it moved with any change instead of validating one. */
    render(<PreparedQueue />);
    await waitFor(() => expect(fetchApprovals).toHaveBeenCalled());
    const [status, type] = fetchApprovals.mock.calls[0];
    expect(status).toBe("pending");
    expect(type).toBeUndefined();
  });

  it("lists each prepared action by title", async () => {
    render(<PreparedQueue />);
    expect(await screen.findByText("Send the term sheet reply to Sarah Chen")).toBeInTheDocument();
  });

  it("says plainly that nothing has run", async () => {
    render(<PreparedQueue />);
    expect(await screen.findByText(/Nothing has run/i)).toBeInTheDocument();
  });

  it("approves a row", async () => {
    render(<PreparedQueue />);
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));
    expect(approveAction).toHaveBeenCalledWith("apr_1");
  });

  it("rejects a row", async () => {
    render(<PreparedQueue />);
    await userEvent.click(await screen.findByRole("button", { name: /reject/i }));
    expect(rejectAction).toHaveBeenCalledWith("apr_1");
  });

  it("removes a decided row so it cannot be decided twice", async () => {
    render(<PreparedQueue />);
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));
    await waitFor(() =>
      expect(screen.queryByText("Send the term sheet reply to Sarah Chen")).toBeNull()
    );
  });

  it("shows an empty state rather than a blank pane", async () => {
    fetchApprovals.mockResolvedValue([]);
    render(<PreparedQueue />);
    expect(await screen.findByText(/Nothing is waiting/i)).toBeInTheDocument();
  });

  it("shows a failure rather than pretending the queue is empty", async () => {
    fetchApprovals.mockRejectedValue(new Error("nope"));
    render(<PreparedQueue />);
    expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
  });

  it("shows a row of every type, labelled", async () => {
    fetchApprovals.mockResolvedValue([
      ROW,
      { ...ROW, approval_id: "apr_2", approval_type: "filter_proposal", title: "Quiet 6 senders?" },
      { ...ROW, approval_id: "apr_3", approval_type: "step:email.send", title: "Send the update" },
    ]);
    render(<PreparedQueue />);
    expect(await screen.findByText("Quiet 6 senders?")).toBeInTheDocument();
    expect(screen.getByText("Send the update")).toBeInTheDocument();
    expect(screen.getByText("Prepared write")).toBeInTheDocument();
    expect(screen.getByText("Filter proposal")).toBeInTheDocument();
    expect(screen.getByText("email.send")).toBeInTheDocument();
  });

  it("only a prepared write claims nothing has run", async () => {
    /* The old copy asserted "Nothing has run" of every row. A step approval
       sits on a run that is already underway. */
    fetchApprovals.mockResolvedValue([
      ROW,
      { ...ROW, approval_id: "apr_2", approval_type: "step:email.send" },
    ]);
    render(<PreparedQueue />);
    await screen.findByText("Prepared write");
    expect(screen.getAllByText(/nothing has run yet/i)).toHaveLength(1);
  });

  it("a chat approval is not offered a button that would 409", async () => {
    /* Those resume a suspended turn via /chat/resume and are refused by these
       endpoints on purpose. */
    fetchApprovals.mockResolvedValue([
      ROW,
      {
        ...ROW,
        approval_id: "apr_chat",
        title: "A chat-turn write",
        decision_route: "chat",
      },
    ]);
    render(<PreparedQueue />);
    await screen.findByText("Send the term sheet reply to Sarah Chen");
    expect(screen.queryByText("A chat-turn write")).toBeNull();
  });

  it("a failed decision costs its own row, not the queue", async () => {
    /* One shared flag replaced the ENTIRE list with "could not be loaded" —
       wrong message, wrong scope, unrecoverable without a remount. */
    fetchApprovals.mockResolvedValue([
      ROW,
      { ...ROW, approval_id: "apr_2", title: "The other one" },
    ]);
    rejectAction.mockRejectedValue(new Error("API 409: cannot decide here"));
    render(<PreparedQueue />);
    await screen.findByText("The other one");
    await userEvent.click(screen.getAllByRole("button", { name: "Reject" })[0]);
    expect(await screen.findByText(/API 409/)).toBeInTheDocument();
    expect(screen.getByText("The other one")).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).toBeNull();
  });
});

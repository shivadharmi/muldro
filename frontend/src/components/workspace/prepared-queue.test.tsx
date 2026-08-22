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

  it("asks only for prepared actions", async () => {
    render(<PreparedQueue />);
    await waitFor(() => expect(fetchApprovals).toHaveBeenCalledWith("pending", "prepared_action"));
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
});

import { test, expect, beforeEach, vi, type Mock } from "vitest";
import { routeApprovalAction } from "./approval-actions";
import { approveAction, rejectAction, editApproval } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  approveAction: vi.fn().mockResolvedValue(undefined),
  rejectAction: vi.fn().mockResolvedValue(undefined),
  editApproval: vi.fn().mockResolvedValue(undefined),
}));

const approveMock = approveAction as unknown as Mock;
const rejectMock = rejectAction as unknown as Mock;
const editMock = editApproval as unknown as Mock;

beforeEach(() => {
  approveMock.mockClear().mockResolvedValue(undefined);
  rejectMock.mockClear().mockResolvedValue(undefined);
  editMock.mockClear().mockResolvedValue(undefined);
});

test("approval.approve routes to approveAction and reports handled", async () => {
  const handled = await routeApprovalAction({
    type: "approval.approve",
    approval_id: "apr_1",
    surface_id: "srf_1",
  });
  expect(handled).toBe(true);
  expect(approveMock).toHaveBeenCalledWith("apr_1");
  expect(rejectMock).not.toHaveBeenCalled();
});

test("approval.reject routes to rejectAction with the reason", async () => {
  const handled = await routeApprovalAction({
    type: "approval.reject",
    approval_id: "apr_2",
    reason: "wrong recipient",
  });
  expect(handled).toBe(true);
  expect(rejectMock).toHaveBeenCalledWith("apr_2", "wrong recipient");
});

test("approval.reject without a reason passes undefined", async () => {
  await routeApprovalAction({ type: "approval.reject", approval_id: "apr_3" });
  expect(rejectMock).toHaveBeenCalledWith("apr_3", undefined);
});

test("approval.edit with a body forwards it to editApproval", async () => {
  const handled = await routeApprovalAction({
    type: "approval.edit",
    approval_id: "apr_4",
    body: { summary: "tweaked" },
  });
  expect(handled).toBe(true);
  expect(editMock).toHaveBeenCalledWith("apr_4", { summary: "tweaked" });
});

test("approval.edit without a body is a no-op but still handled (v1 limitation)", async () => {
  const handled = await routeApprovalAction({
    type: "approval.edit",
    approval_id: "apr_5",
  });
  expect(handled).toBe(true);
  expect(editMock).not.toHaveBeenCalled();
});

test("a non-approval action falls through (returns false, no REST call)", async () => {
  const handled = await routeApprovalAction({
    type: "execute_insight",
    surface_id: "srf_9",
  });
  expect(handled).toBe(false);
  expect(approveMock).not.toHaveBeenCalled();
  expect(rejectMock).not.toHaveBeenCalled();
  expect(editMock).not.toHaveBeenCalled();
});

test("a payload with no type at all falls through", async () => {
  const handled = await routeApprovalAction({ surface_id: "srf_10" });
  expect(handled).toBe(false);
});

test("an approval action missing approval_id reports an error but stays handled", async () => {
  const onError = vi.fn();
  const handled = await routeApprovalAction(
    { type: "approval.approve" },
    onError,
  );
  expect(handled).toBe(true);
  expect(approveMock).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledTimes(1);
});

test("a REST failure is surfaced via onError but the action stays handled", async () => {
  approveMock.mockRejectedValueOnce(new Error("network down"));
  const onError = vi.fn();
  const handled = await routeApprovalAction(
    { type: "approval.approve", approval_id: "apr_6" },
    onError,
  );
  expect(handled).toBe(true);
  expect(onError).toHaveBeenCalledWith("network down");
});

test("onSuccess fires only on a successful approve", async () => {
  const onSuccess = vi.fn();
  await routeApprovalAction(
    { type: "approval.approve", approval_id: "apr_7" },
    undefined,
    onSuccess,
  );
  expect(onSuccess).toHaveBeenCalledTimes(1);
});

test("onSuccess does NOT fire when the REST call fails", async () => {
  rejectMock.mockRejectedValueOnce(new Error("boom"));
  const onSuccess = vi.fn();
  await routeApprovalAction(
    { type: "approval.reject", approval_id: "apr_8" },
    undefined,
    onSuccess,
  );
  expect(onSuccess).not.toHaveBeenCalled();
});

test("approval.edit without a body reports via onInfo (not onSuccess)", async () => {
  const onSuccess = vi.fn();
  const onInfo = vi.fn();
  await routeApprovalAction(
    { type: "approval.edit", approval_id: "apr_9" },
    undefined,
    onSuccess,
    onInfo,
  );
  expect(editMock).not.toHaveBeenCalled();
  expect(onSuccess).not.toHaveBeenCalled();
  expect(onInfo).toHaveBeenCalledTimes(1);
});

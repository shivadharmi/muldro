/** Routes A2UI approval-button actions to the REST approval endpoints.
 *
 * A2UI approval buttons (rendered server-side via `units.approval_card`) emit
 *   onAction("click", { type: "approval.{approve|reject}", approval_id, surface_id })
 * These must go to the REST endpoints in `lib/api.ts` (POST /v1/approvals/{id}/...),
 * NOT to the WebSocket action registry (which only knows the live InlineApprovalCard
 * frames "approve"/"reject" with `{id}`). This helper isolates that routing decision
 * so the surface-detail modal can try it first and fall through to the WS handler for
 * any non-approval action.
 */

import { approveAction, rejectAction } from "@/lib/api";

/** Known approval action discriminators emitted by the A2UI approval card. */
const APPROVAL_ACTION_TYPES = ["approval.approve", "approval.reject"] as const;

type ApprovalActionType = (typeof APPROVAL_ACTION_TYPES)[number];

function isApprovalActionType(value: unknown): value is ApprovalActionType {
  return (
    typeof value === "string" &&
    (APPROVAL_ACTION_TYPES as readonly string[]).includes(value)
  );
}

/** Optional error reporter so callers can surface failures via their toast store. */
export type ApprovalErrorReporter = (message: string) => void;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Approval action failed";
}

/**
 * Inspects an A2UI action payload and, if it is an approval action, dispatches it
 * to the matching REST endpoint.
 *
 * @returns `true` if the payload was an approval action that this helper handled
 *   (successfully or not — see `onError`); `false` if it was not an approval action
 *   and the caller should fall through to its normal (WS) action handler.
 *
 * On a failed REST call the error is reported via `onError` (or `console.error` when
 * none is supplied) and `true` is still returned, because the action *was* an approval
 * action — the caller must not also route it to the WS handler.
 */
export async function routeApprovalAction(
  payload: Record<string, unknown>,
  onError?: ApprovalErrorReporter,
  onSuccess?: () => void,
): Promise<boolean> {
  const actionType = payload.type;
  if (!isApprovalActionType(actionType)) return false;

  const approvalId = payload.approval_id;
  if (typeof approvalId !== "string" || approvalId.length === 0) {
    const msg = "Approval action is missing an approval_id";
    if (onError) onError(msg);
    else console.error(msg, payload);
    return true;
  }

  const reason = typeof payload.reason === "string" ? payload.reason : undefined;

  try {
    switch (actionType) {
      case "approval.approve":
        await approveAction(approvalId);
        onSuccess?.();
        break;
      case "approval.reject":
        await rejectAction(approvalId, reason);
        onSuccess?.();
        break;
    }
  } catch (error: unknown) {
    const msg = errorMessage(error);
    if (onError) onError(msg);
    else console.error(msg, error);
  }

  return true;
}

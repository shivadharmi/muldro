"""Approval endpoints — approve/reject pending actions."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_current_user
from src.api.schemas import ApprovalDecisionRequest, ApprovalResponse

router = APIRouter()


@router.get("/v1/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str = "pending",
    user_id: str = Depends(get_current_user),
):
    """List pending approvals for the user."""
    # TODO: Wire to approval service
    return []


@router.post("/v1/approvals/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user),
):
    """Approve a pending action."""
    # TODO: Wire to execution service
    raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")


@router.post("/v1/approvals/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user),
):
    """Reject a pending action."""
    # TODO: Wire to execution service
    raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

"""The founder's filters: what they granted, and taking it back.

A rule is an authority muldro holds because a human said so. Three things
follow, and each is an endpoint here: it must be LISTABLE (you cannot revoke
what you cannot see), EXPLICABLE (which rule hid this?), and REVOCABLE.

Creation is deliberately absent. A rule exists only by confirming a proposal —
`POST /v1/approvals/{id}/approve` — so there is no way to mint one directly,
and `FilterRule.created_from_approval_id` is NOT NULL to keep that checkable
rather than merely intended.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.filter_rule import FilterRule
from src.services.filter_rules import revoke_rule

router = APIRouter()
logger = logging.getLogger(__name__)


class FilterRuleResponse(BaseModel):
    rule_id: str
    source: str
    match_kind: str
    match_value: str
    enabled: bool
    created_at: datetime | None = None
    revoked_at: datetime | None = None
    # The approval the founder answered. Carried so a rule can always be traced
    # back to the decision that created it.
    created_from_approval_id: str


class FilterRuleListResponse(BaseModel):
    rules: list[FilterRuleResponse]
    count: int


class RevokeResponse(BaseModel):
    rule_id: str
    released: int


def _shape(rule: FilterRule) -> FilterRuleResponse:
    return FilterRuleResponse(
        rule_id=rule.rule_id,
        source=rule.source,
        match_kind=rule.match_kind,
        match_value=rule.match_value,
        enabled=rule.enabled,
        created_at=rule.created_at,
        revoked_at=rule.revoked_at,
        created_from_approval_id=rule.created_from_approval_id,
    )


@router.get("/v1/workspace/filter-rules", response_model=FilterRuleListResponse)
async def list_filter_rules(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> FilterRuleListResponse:
    """Every rule, live and revoked.

    Revoked rules are included rather than hidden: they are the record of what
    was once being filtered, and a founder deciding whether to re-enable one
    needs to see it.
    """
    rows = list(
        (
            await db.execute(
                select(FilterRule)
                .where(FilterRule.workspace_id == workspace_id)
                .order_by(FilterRule.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return FilterRuleListResponse(rules=[_shape(r) for r in rows], count=len(rows))


@router.delete("/v1/workspace/filter-rules/{rule_id}", response_model=RevokeResponse)
async def revoke_filter_rule(
    rule_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> RevokeResponse:
    """Turn a rule off and RELEASE the mail it hid.

    DELETE by verb, not by effect: the row is kept. A deleted rule loses the
    evidence of what it once hid, and the founder may want it back.

    `released` is the count of events whose frozen triage verdict was cleared.
    Without that step, revoking would leave the mail unactionable — and
    therefore folded — for ever, with the rule that caused it already gone.
    """
    released = await revoke_rule(
        db, workspace_id=workspace_id, rule_id=rule_id, now=datetime.now(timezone.utc)
    )
    if released == 0:
        exists = (
            (
                await db.execute(
                    select(FilterRule).where(
                        FilterRule.workspace_id == workspace_id,
                        FilterRule.rule_id == rule_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="No such filter rule.")
    await db.commit()
    logger.info(
        "filter_rule_revoked_by_user workspace=%s user=%s rule=%s released=%d",
        workspace_id,
        user_id,
        rule_id,
        released,
    )
    return RevokeResponse(rule_id=rule_id, released=released)

"""Approval surface detail tab builders (trust context)."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailTabResponse

from ._shared import (
    _empty_tab,
    _extract_approval_id,
    _format_ts,
    _section,
    _truncate,
)

logger = logging.getLogger(__name__)


async def build_approval_request(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("request", "No linked approval.")

    result = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    apr = result.scalar_one_or_none()
    if not apr:
        return _empty_tab("request", f"Approval {approval_id[:16]}... not found.")

    children: list[A2UIComponent] = [
        r.text("apr_title", apr.title or "Approval Request"),
        r.text("apr_summary", apr.summary or "No details available."),
    ]
    if apr.artifact_refs and isinstance(apr.artifact_refs, dict):
        # TrustGate step approvals store "capability"/"step_name"; tool-level approvals
        # store "tool_name"/"tool_params". Accept either so both render tool context.
        tool_name = apr.artifact_refs.get("tool_name") or apr.artifact_refs.get("capability", "")
        if tool_name:
            children.append(r.badge("apr_tool", f"Tool: {tool_name}"))
        tool_params = apr.artifact_refs.get("tool_params")
        if tool_params:
            children.append(r.code_block("apr_params", str(tool_params), language="json"))
        tool_input = apr.artifact_refs.get("tool_input")
        if tool_input:
            children.append(r.code_block("apr_input", str(tool_input), language="json"))
            if apr.artifact_refs.get("tool_input_truncated"):
                children.append(
                    r.caption(
                        "apr_input_clipped",
                        "Payload clipped for storage — showing the start.",
                    )
                )

    risk_variant = "warning" if apr.risk_level in ("high", "critical") else "default"
    children.append(r.badge("apr_risk", apr.risk_level or "medium", variant=risk_variant))

    # Trust context
    if apr.artifact_refs and isinstance(apr.artifact_refs, dict):
        cap = apr.artifact_refs.get("tool_name")
        if cap:
            from src.models.trust_state import TrustState

            trust_result = await db.execute(
                select(TrustState).where(
                    TrustState.workspace_id == apr.workspace_id,
                    TrustState.capability == cap,
                    TrustState.risk_level == (apr.risk_level or "low"),
                )
            )
            trust_state = trust_result.scalar_one_or_none()
            if trust_state:
                level = trust_state.trust_level
                count = trust_state.approved_count
                if level == "first_use":
                    children.append(r.badge("apr_trust", "First time", variant="default"))
                elif level == "learning":
                    remaining = max(0, 10 - count)
                    children.append(
                        r.text(
                            "apr_trust_hint",
                            f"Similar to {count} prior approvals — "
                            f"{remaining} more to auto-approve",
                        )
                    )
                else:
                    children.append(
                        r.badge(
                            "apr_trust",
                            level.title(),
                            variant="success",
                        )
                    )
            else:
                children.append(r.badge("apr_trust", "First time", variant="default"))

    if apr.status == "pending":
        children.append(
            r.row(
                "apr_actions",
                [
                    r.button(
                        f"approve_{apr.approval_id}",
                        "Approve",
                        variant="primary",
                        action_payload={
                            "type": "approval.approve",
                            "approval_id": apr.approval_id,
                        },
                    ),
                    r.button(
                        f"reject_{apr.approval_id}",
                        "Reject",
                        variant="danger",
                        action_payload={
                            "type": "approval.reject",
                            "approval_id": apr.approval_id,
                        },
                    ),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="request",
        sections=[_section("details", "Request Details", children, collapsed=False)],
    )


async def build_approval_risk(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("risk", "No linked approval.")

    result = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    apr = result.scalar_one_or_none()
    if not apr:
        return _empty_tab("risk", "Approval not found.")

    children: list[A2UIComponent] = []
    risk_variant = "danger" if apr.risk_level in ("high", "critical") else "warning"
    children.append(r.badge("risk_level", apr.risk_level or "medium", variant=risk_variant))

    if apr.summary:
        children.append(r.text("risk_just", apr.summary))
    if apr.decision_reason:
        children.append(r.text("risk_reason", apr.decision_reason))
    if apr.status and apr.status != "pending":
        children.append(r.badge("risk_decision", apr.status))

    return DetailTabResponse(
        tab_id="risk",
        sections=[_section("assessment", "Risk Assessment", children, collapsed=False)],
    )


async def build_approval_history(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Approval history — past approvals of the same type."""
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("history", "No linked approval.")

    # Get the current approval to find its type
    curr = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    current = curr.scalar_one_or_none()
    if not current:
        return _empty_tab("history", "Approval not found.")

    # Find past approvals of the same type
    result = await db.execute(
        select(Approval)
        .where(
            Approval.workspace_id == current.workspace_id,
            Approval.approval_type == current.approval_type,
            Approval.approval_id != approval_id,
            Approval.status.in_(["approved", "rejected"]),
        )
        .order_by(Approval.decided_at.desc())
        .limit(10)
    )
    past = list(result.scalars().all())

    if not past:
        return _empty_tab("history", "No prior approvals of this type.")

    children: list[A2UIComponent] = []
    for i, apr in enumerate(past):
        variant = "success" if apr.status == "approved" else "danger"
        children.append(
            r.row(
                f"hist_{i}",
                [
                    r.badge(f"hist_{i}_st", apr.status, variant=variant),
                    r.text(f"hist_{i}_title", _truncate(apr.title or "", 60)),
                    r.caption(f"hist_{i}_time", _format_ts(apr.decided_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="history",
        sections=[_section("past", f"Past Decisions ({len(past)})", children, collapsed=False)],
    )

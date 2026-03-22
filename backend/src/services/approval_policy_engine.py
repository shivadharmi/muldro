"""Approval policy engine — evaluates whether a tool/capability needs approval.

Checks ApprovalPolicy records for the workspace, matching by capability pattern.
Patterns support wildcards: "email.*" matches "email.send", "email.draft", etc.
"""

from __future__ import annotations

import fnmatch
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approval_policy import ApprovalPolicy

logger = logging.getLogger(__name__)


class ApprovalDecision:
    """Result of an approval policy check."""

    __slots__ = ("requires_approval", "policy_id", "reason")

    def __init__(
        self,
        requires_approval: bool,
        policy_id: str | None = None,
        reason: str = "",
    ):
        self.requires_approval = requires_approval
        self.policy_id = policy_id
        self.reason = reason


class ApprovalPolicyEngine:
    """Evaluates approval policies for a workspace."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id
        self._policies: list[ApprovalPolicy] | None = None

    async def _load_policies(self) -> list[ApprovalPolicy]:
        """Load and cache enabled policies for this workspace."""
        if self._policies is None:
            result = await self._db.execute(
                select(ApprovalPolicy).where(
                    ApprovalPolicy.workspace_id == self._workspace_id,
                    ApprovalPolicy.enabled.is_(True),
                )
            )
            self._policies = list(result.scalars().all())
        return self._policies

    async def check(
        self,
        capability: str | None,
        tool_name: str,
        risk_level: str = "low",
        trust_tier: str | None = None,
    ) -> ApprovalDecision:
        """Check if a tool/capability requires approval based on workspace policies.

        Args:
            capability: Canonical capability (e.g., "email.send")
            tool_name: Raw tool name (e.g., "gmail_send_email")
            risk_level: Risk level of the tool (low, medium, high, critical)
            trust_tier: Trust tier of the backend (T0, T1, T2, T3)

        Returns:
            ApprovalDecision with requires_approval flag, policy_id, and reason.
        """
        policies = await self._load_policies()
        if not policies:
            return ApprovalDecision(requires_approval=False, reason="no policies configured")

        # Find matching policies (capability pattern match)
        matches: list[ApprovalPolicy] = []
        for policy in policies:
            pattern = policy.capability_pattern
            # Match against capability or tool_name
            if capability and fnmatch.fnmatch(capability, pattern):
                matches.append(policy)
            elif fnmatch.fnmatch(tool_name, pattern):
                matches.append(policy)
            elif pattern == "*":
                matches.append(policy)

        if not matches:
            return ApprovalDecision(requires_approval=False, reason="no matching policy")

        # Evaluate each matching policy — most restrictive wins
        for policy in matches:
            # "never" mode — skip approval regardless
            if policy.approval_mode == "never":
                continue

            # Check trust tier exemption
            if trust_tier and policy.trust_tier_min:
                tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
                if tier_order.get(trust_tier, 3) <= tier_order.get(policy.trust_tier_min, 3):
                    continue  # trusted enough to skip

            # "always" mode — require approval
            if policy.approval_mode == "always":
                return ApprovalDecision(
                    requires_approval=True,
                    policy_id=policy.policy_id,
                    reason=f"policy '{policy.capability_pattern}' requires approval (always)",
                )

            # "high_risk_only" mode — check risk threshold
            if policy.approval_mode == "high_risk_only":
                risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
                threshold = risk_order.get(policy.risk_threshold or "high", 2)
                actual = risk_order.get(risk_level, 0)
                if actual >= threshold:
                    return ApprovalDecision(
                        requires_approval=True,
                        policy_id=policy.policy_id,
                        reason=(
                            f"policy '{policy.capability_pattern}' requires approval"
                            f" (risk {risk_level} >= {policy.risk_threshold})"
                        ),
                    )

        return ApprovalDecision(
            requires_approval=False, reason="policies evaluated, no approval needed"
        )

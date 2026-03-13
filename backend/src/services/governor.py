"""Governor — enforces trust and safety policies.

Sits before every meaningful execution. Evaluates whether an action
is allowed, needs approval, or should be blocked.

Responsibilities:
- Evaluate action policies
- Determine execution mode (auto_execute, approval_required, blocked)
- Route to approval creation when needed
- Log policy decisions to audit trail
"""


class Governor:
    """Evaluate plans against safety policies."""

    async def evaluate_plan(self, plan_id: str, user_id: str) -> str:
        """Evaluate a plan and return execution mode.

        Returns: 'auto_execute', 'approval_required', or 'blocked'
        """
        # TODO: Implement
        # For v1: all external writes are approval_required
        return "approval_required"

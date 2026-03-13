"""Planner — decides what should happen next.

The Planner is the brain of Jarvis. It reads events, world model state,
and memory to produce structured task graphs.

Responsibilities:
- Interpret context from events and user commands
- Decide: ignore / add_to_brief / summarize_now / create_task / draft_reply / etc.
- Produce structured Plan with PlanTasks
- Set execution mode (approval_required, draft_only, auto_execute)

The Planner uses Claude for reasoning but MUST output structured JSON,
not free-form text. The plan contract is the most important schema.
"""


class Planner:
    """Generate structured plans from events or user commands."""

    async def plan_for_event(self, event_id: str, user_id: str) -> str | None:
        """Create a plan in response to a normalized event. Returns plan_id."""
        # TODO: Implement
        # 1. Fetch event details
        # 2. Fetch relevant entities from world model
        # 3. Retrieve relevant memories
        # 4. Call Claude with structured output schema
        # 5. Store Plan + PlanTasks
        # 6. Route to Governor for policy check
        return None

    async def plan_for_command(
        self, command: str, user_id: str, context: str | None = None
    ) -> str | None:
        """Create a plan from a direct user command. Returns plan_id."""
        # TODO: Implement
        # Same flow as plan_for_event but triggered by user input
        return None

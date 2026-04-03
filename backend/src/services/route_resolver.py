"""RouteResolver — intent-based agent routing, replacing hardcoded if/elif.

Loads routes from DB, evaluates decision dicts against route conditions,
and returns an ordered agent pipeline for execution.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.agent_routes import AgentRoute

logger = logging.getLogger(__name__)

# Default routes matching the current hardcoded behavior.
# These are seeded on first startup.
DEFAULT_ROUTES: list[dict[str, Any]] = [
    {
        "name": "create_task",
        "description": "Route for task creation — governance check, then execution.",
        "decision_type": "create_task",
        "agent_pipeline": [
            {"agent": "governor", "message_template": "Evaluate this plan: {decision_json}"},
            {
                "agent": "operator",
                "condition": {"has_truthy_key": "plan_id"},
                "action": "execute_plan",
            },
        ],
        "priority": 100,
        "keywords": ["create", "do", "execute", "send", "schedule", "draft"],
    },
    {
        "name": "research",
        "description": "Route for research requests — deep context gathering.",
        "decision_type": "research",
        "agent_pipeline": [
            {
                "agent": "researcher",
                "message_template": "Research this: {decision_json}",
            },
        ],
        "priority": 90,
        "keywords": ["research", "find", "look up", "investigate", "analyze"],
    },
    {
        "name": "read_source",
        "description": "Route for reading data from external sources.",
        "decision_type": "read_source",
        "agent_pipeline": [
            {
                "agent": "observer",
                "message_template": "Read and report from external sources: {decision_json}",
            },
            {
                "agent": "presenter",
                "message_template": "Present observation results ({surface}): {decision_json}",
            },
        ],
        "priority": 95,
        "keywords": [
            "check email",
            "check gmail",
            "check inbox",
            "read email",
            "list emails",
            "check calendar",
            "list events",
            "upcoming meetings",
            "check github",
            "list PRs",
            "list issues",
            "check PRs",
            "check slack",
            "read messages",
            "check messages",
            "fetch",
            "get latest",
            "show me",
        ],
    },
    {
        "name": "observe",
        "description": "Route for background observation — monitor and scan sources.",
        "decision_type": "observe",
        "agent_pipeline": [
            {
                "agent": "observer",
                "message_template": "Observe external sources: {decision_json}",
            },
        ],
        "priority": 85,
        "keywords": ["monitor", "watch", "observe", "scan", "poll"],
    },
    {
        "name": "remember",
        "description": "Route for memory/entity operations.",
        "decision_type": "remember",
        "agent_pipeline": [
            {
                "agent": "librarian",
                "message_template": "Update knowledge: {decision_json}",
            },
        ],
        "priority": 90,
        "keywords": ["remember", "store", "save", "update entity", "note"],
    },
    {
        "name": "ask_user",
        "description": "Route for clarification — present question to user.",
        "decision_type": "ask_user",
        "agent_pipeline": [],
        "priority": 80,
        "keywords": ["clarify", "ask", "confirm"],
    },
    {
        "name": "recommend",
        "description": "Route for recommendations — format for user.",
        "decision_type": "recommend",
        "agent_pipeline": [],
        "priority": 80,
        "keywords": ["recommend", "suggest", "advise"],
    },
    {
        "name": "summarize",
        "description": "Route for summarization — format for user.",
        "decision_type": "summarize",
        "agent_pipeline": [],
        "priority": 80,
        "keywords": ["summarize", "brief", "overview", "status"],
    },
    {
        "name": "watcher_create",
        "description": "Route for watcher/trigger creation — observation setup.",
        "decision_type": "watcher_create",
        "agent_pipeline": [
            {
                "agent": "observer",
                "message_template": "Set up a watcher: {decision_json}",
            },
        ],
        "priority": 85,
        "keywords": ["watch", "alert", "notify when", "trigger", "monitor"],
    },
    {
        "name": "goal_update",
        "description": "Route for goal/objective updates.",
        "decision_type": "goal_update",
        "agent_pipeline": [
            {
                "agent": "planner",
                "message_template": "Update or create goal: {decision_json}",
            },
        ],
        "priority": 85,
        "keywords": ["goal", "objective", "target", "milestone"],
    },
    {
        "name": "draft_reply",
        "description": "Route for drafting replies — Operator reads thread + drafts via tools.",
        "decision_type": "draft_reply",
        "agent_pipeline": [
            {"agent": "governor", "message_template": "Evaluate this plan: {decision_json}"},
            {
                "agent": "operator",
                "message_template": (
                    "Draft an email reply. Read the original thread first, "
                    "then create a draft. Decision: {decision_json}"
                ),
            },
        ],
        "priority": 95,
        "keywords": ["draft", "reply", "compose", "write email", "respond"],
    },
    {
        "name": "schedule_reminder",
        "description": "Route for scheduling reminders.",
        "decision_type": "schedule_reminder",
        "agent_pipeline": [],
        "priority": 85,
        "keywords": ["remind", "reminder", "alert me", "notify me at", "schedule"],
    },
    {
        "name": "add_to_brief",
        "description": "Route for adding items to the next briefing.",
        "decision_type": "add_to_brief",
        "agent_pipeline": [
            {
                "agent": "librarian",
                "message_template": "Store this as a briefing item for the user: {decision_json}",
            },
        ],
        "priority": 85,
        "keywords": ["brief", "briefing", "add to brief", "morning update"],
    },
    {
        "name": "answer_directly",
        "description": "Route for direct answers from context — Presenter only.",
        "decision_type": "answer_directly",
        "agent_pipeline": [],
        "priority": 80,
        "keywords": [],
    },
    {
        "name": "search_memory",
        "description": "Route for knowledge search — Researcher gathers, Presenter formats.",
        "decision_type": "search_memory",
        "agent_pipeline": [
            {
                "agent": "researcher",
                "message_template": "Search memories and knowledge for: {decision_json}",
            },
        ],
        "priority": 80,
        "keywords": ["recall", "what do you know", "search memory"],
    },
    {
        "name": "ignore",
        "description": "Silently ignore — no response, no action.",
        "decision_type": "ignore",
        "agent_pipeline": [],
        "priority": 5,
        "keywords": [],
    },
    {
        "name": "acknowledge",
        "description": "Default fallback — just acknowledge.",
        "decision_type": "acknowledge",
        "agent_pipeline": [],
        "priority": 10,
        "keywords": [],
    },
]

# Decisions that always go through the presenter for user-facing output
ALWAYS_PRESENT = {
    "ask_user",
    "recommend",
    "summarize",
    "acknowledge",
    "research",
    "read_source",
    "draft_reply",
    "answer_directly",
    "search_memory",
    "add_to_brief",
    "schedule_reminder",
}


class RouteResolver:
    """Resolve planner decisions to agent pipelines using DB-backed routes."""

    def __init__(self, db: AsyncSession):
        self._db = db
        self._cache: list[AgentRoute] | None = None

    async def seed_defaults(self) -> int:
        """Seed or update default routes. Returns count created/updated.

        Creates new routes that don't exist. For existing routes, syncs
        agent_pipeline, priority, conditions, and keywords from DEFAULT_ROUTES
        so code changes propagate without manual DB migration.
        """
        result = await self._db.execute(select(AgentRoute))
        existing = {route.name: route for route in result.scalars().all()}

        changed = 0
        for route_def in DEFAULT_ROUTES:
            name = route_def["name"]

            if name not in existing:
                route = AgentRoute(
                    route_id=f"rte_{ULID()}",
                    name=name,
                    description=route_def.get("description"),
                    decision_type=route_def["decision_type"],
                    agent_pipeline=route_def["agent_pipeline"],
                    conditions=route_def.get("conditions"),
                    priority=route_def.get("priority", 100),
                    enabled=True,
                    keywords=route_def.get("keywords"),
                    weight=route_def.get("weight", 1.0),
                )
                self._db.add(route)
                changed += 1
                continue

            # Sync mutable fields if they diverged from defaults
            route = existing[name]
            needs_update = False

            if route.agent_pipeline != route_def["agent_pipeline"]:
                route.agent_pipeline = route_def["agent_pipeline"]
                needs_update = True
            if route.priority != route_def.get("priority", 100):
                route.priority = route_def.get("priority", 100)
                needs_update = True
            if route.conditions != route_def.get("conditions"):
                route.conditions = route_def.get("conditions")
                needs_update = True
            if route.keywords != route_def.get("keywords"):
                route.keywords = route_def.get("keywords")
                needs_update = True
            if route.description != route_def.get("description"):
                route.description = route_def.get("description")
                needs_update = True

            if needs_update:
                changed += 1

        if changed:
            await self._db.flush()
            logger.info("Seeded/updated %d agent routes", changed)

        return changed

    async def resolve(self, decision: dict) -> list[dict]:
        """Resolve a planner decision to an agent pipeline.

        Returns a list of pipeline steps, each with:
        - agent: str — agent name
        - message_template: str — template for the agent message
        - condition: dict | None — optional condition to check
        - action: str | None — special action (e.g., "execute_plan")

        The caller (orchestrator) is responsible for:
        1. Iterating through the pipeline
        2. Formatting messages
        3. Checking step conditions
        4. Always appending presenter + persona at the end
        """
        decision_type = decision.get("decision_type") or decision.get("decision", "acknowledge")
        routes = await self._get_routes()

        # Find all matching routes for this decision type
        candidates = [
            r
            for r in routes
            if r.enabled
            and r.decision_type == decision_type
            and self._matches_conditions(r, decision)
        ]

        if not candidates:
            # Fallback: try to find the "acknowledge" route
            candidates = [r for r in routes if r.enabled and r.decision_type == "acknowledge"]

        if not candidates:
            return []

        # Pick highest priority, then highest weight
        best = max(candidates, key=lambda r: (r.priority, r.weight))
        return best.agent_pipeline or []

    def _matches_conditions(self, route: AgentRoute, decision: dict) -> bool:
        """Check if a route's conditions match the decision."""
        if not route.conditions:
            return True

        conditions = route.conditions
        for key, value in conditions.items():
            if key == "has_key":
                if value not in decision:
                    return False
            elif key == "has_truthy_key":
                if not decision.get(value):
                    return False
            elif key == "not_has_key":
                if value in decision:
                    return False
            elif key.startswith("field:"):
                field_name = key.split(":", 1)[1]
                if decision.get(field_name) != value:
                    return False
            else:
                # Direct key=value check
                if decision.get(key) != value:
                    return False

        return True

    async def _get_routes(self) -> list[AgentRoute]:
        """Get all routes, using cache if available."""
        if self._cache is not None:
            return self._cache

        result = await self._db.execute(
            select(AgentRoute)
            .where(AgentRoute.enabled.is_(True))
            .order_by(AgentRoute.priority.desc(), AgentRoute.weight.desc())
        )
        self._cache = list(result.scalars().all())
        return self._cache

    def invalidate_cache(self) -> None:
        """Invalidate the route cache (call after mutations)."""
        self._cache = None

    async def list_routes(self, include_disabled: bool = False) -> list[AgentRoute]:
        """List all routes."""
        stmt = select(AgentRoute).order_by(AgentRoute.priority.desc(), AgentRoute.name)
        if not include_disabled:
            stmt = stmt.where(AgentRoute.enabled.is_(True))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_route(self, route_id: str) -> AgentRoute | None:
        """Get a route by ID."""
        result = await self._db.execute(select(AgentRoute).where(AgentRoute.route_id == route_id))
        return result.scalar_one_or_none()

    async def create_route(
        self,
        name: str,
        decision_type: str,
        agent_pipeline: list[dict],
        *,
        description: str | None = None,
        conditions: dict | None = None,
        priority: int = 100,
        keywords: list[str] | None = None,
        weight: float = 1.0,
    ) -> AgentRoute:
        """Create a new route."""
        route = AgentRoute(
            route_id=f"rte_{ULID()}",
            name=name,
            description=description,
            decision_type=decision_type,
            agent_pipeline=agent_pipeline,
            conditions=conditions,
            priority=priority,
            enabled=True,
            keywords=keywords,
            weight=weight,
        )
        self._db.add(route)
        await self._db.flush()
        self.invalidate_cache()
        return route

    async def update_route(self, route_id: str, updates: dict) -> AgentRoute | None:
        """Update a route. Returns updated route or None."""
        route = await self.get_route(route_id)
        if not route:
            return None

        allowed = {
            "name",
            "description",
            "decision_type",
            "agent_pipeline",
            "conditions",
            "priority",
            "enabled",
            "keywords",
            "weight",
        }
        for key, value in updates.items():
            if key in allowed:
                setattr(route, key, value)

        await self._db.flush()
        await self._db.refresh(route)
        self.invalidate_cache()
        return route

    async def delete_route(self, route_id: str) -> bool:
        """Delete a route. Returns True if deleted."""
        route = await self.get_route(route_id)
        if not route:
            return False
        await self._db.delete(route)
        await self._db.flush()
        self.invalidate_cache()
        return True

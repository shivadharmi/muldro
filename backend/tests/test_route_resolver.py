"""Tests for Phase 6 — Dynamic Agent Routing.

Tests RouteResolver: seeding, resolution, condition matching,
priority ordering, CRUD, and orchestrator integration.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.route_resolver import DEFAULT_ROUTES, RouteResolver


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    result_mock = MagicMock()
    result_mock.all.return_value = []
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ── Default Routes ──────────────────────────────────────────────


class TestDefaultRoutes:
    def test_default_routes_cover_all_decisions(self):
        """Default routes should cover the main decision types."""
        decision_types = {r["decision_type"] for r in DEFAULT_ROUTES}
        assert "create_task" in decision_types
        assert "research" in decision_types
        assert "observe" in decision_types
        assert "remember" in decision_types
        assert "ask_user" in decision_types
        assert "recommend" in decision_types
        assert "summarize" in decision_types
        assert "acknowledge" in decision_types
        assert "watcher_create" in decision_types
        assert "goal_update" in decision_types

    def test_default_routes_have_10_entries(self):
        """Should have 10 default routes (8 original + watcher_create + goal_update)."""
        assert len(DEFAULT_ROUTES) == 10

    def test_create_task_route_has_governor_and_operator(self):
        """create_task route should pipeline through governor then operator."""
        route = next(r for r in DEFAULT_ROUTES if r["decision_type"] == "create_task")
        agents = [step["agent"] for step in route["agent_pipeline"]]
        assert agents == ["governor", "operator"]

    def test_watcher_create_route_uses_observer(self):
        """watcher_create route should pipeline through observer."""
        route = next(r for r in DEFAULT_ROUTES if r["decision_type"] == "watcher_create")
        agents = [step["agent"] for step in route["agent_pipeline"]]
        assert agents == ["observer"]

    def test_goal_update_route_uses_planner(self):
        """goal_update route should pipeline through planner."""
        route = next(r for r in DEFAULT_ROUTES if r["decision_type"] == "goal_update")
        agents = [step["agent"] for step in route["agent_pipeline"]]
        assert agents == ["planner"]


# ── Seeding ────────────────────────────────────────────────────


class TestRouteSeeding:
    @pytest.mark.asyncio
    async def test_seed_defaults_creates_routes(self, mock_db):
        """Should seed all 10 default routes when none exist."""
        # No existing routes
        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        count = await resolver.seed_defaults()

        assert count == 10
        assert mock_db.add.call_count == 10

    @pytest.mark.asyncio
    async def test_seed_defaults_skips_existing(self, mock_db):
        """Should skip routes that already exist."""
        result_mock = MagicMock()
        # Simulate 3 existing routes
        result_mock.all.return_value = [("create_task",), ("research",), ("observe",)]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        count = await resolver.seed_defaults()

        assert count == 7  # 10 - 3 existing


# ── Resolution ─────────────────────────────────────────────────


class TestRouteResolution:
    @pytest.mark.asyncio
    async def test_resolve_create_task(self, mock_db):
        """Should resolve create_task to governor+operator pipeline."""
        route = MagicMock()
        route.enabled = True
        route.decision_type = "create_task"
        route.agent_pipeline = [
            {"agent": "governor", "message_template": "Evaluate: {decision_json}"},
            {"agent": "operator", "condition": {"has_key": "plan_id"}, "action": "execute_plan"},
        ]
        route.conditions = None
        route.priority = 100
        route.weight = 1.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [route]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        pipeline = await resolver.resolve({"decision": "create_task", "plan_id": "plan_001"})

        assert len(pipeline) == 2
        assert pipeline[0]["agent"] == "governor"
        assert pipeline[1]["agent"] == "operator"

    @pytest.mark.asyncio
    async def test_resolve_falls_back_to_acknowledge(self, mock_db):
        """Should fall back to acknowledge route for unknown decisions."""
        ack_route = MagicMock()
        ack_route.enabled = True
        ack_route.decision_type = "acknowledge"
        ack_route.agent_pipeline = []
        ack_route.conditions = None
        ack_route.priority = 10
        ack_route.weight = 1.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ack_route]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        pipeline = await resolver.resolve({"decision": "unknown_decision"})

        assert pipeline == []

    @pytest.mark.asyncio
    async def test_resolve_empty_when_no_routes(self, mock_db):
        """Should return empty pipeline when no routes match."""
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        pipeline = await resolver.resolve({"decision": "nonexistent"})

        assert pipeline == []


# ── Condition Matching ─────────────────────────────────────────


class TestConditionMatching:
    def test_no_conditions_always_matches(self):
        resolver = RouteResolver(MagicMock())
        route = MagicMock()
        route.conditions = None
        assert resolver._matches_conditions(route, {"decision": "test"}) is True

    def test_has_key_condition_matches(self):
        resolver = RouteResolver(MagicMock())
        route = MagicMock()
        route.conditions = {"has_key": "plan_id"}
        assert resolver._matches_conditions(route, {"plan_id": "p1"}) is True
        assert resolver._matches_conditions(route, {"other": "val"}) is False

    def test_not_has_key_condition(self):
        resolver = RouteResolver(MagicMock())
        route = MagicMock()
        route.conditions = {"not_has_key": "error"}
        assert resolver._matches_conditions(route, {"plan_id": "p1"}) is True
        assert resolver._matches_conditions(route, {"error": "oops"}) is False

    def test_field_condition(self):
        resolver = RouteResolver(MagicMock())
        route = MagicMock()
        route.conditions = {"field:source": "email"}
        assert resolver._matches_conditions(route, {"source": "email"}) is True
        assert resolver._matches_conditions(route, {"source": "slack"}) is False

    def test_direct_key_value_condition(self):
        resolver = RouteResolver(MagicMock())
        route = MagicMock()
        route.conditions = {"decision": "create_task"}
        assert resolver._matches_conditions(route, {"decision": "create_task"}) is True
        assert resolver._matches_conditions(route, {"decision": "research"}) is False


# ── Priority Ordering ──────────────────────────────────────────


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_higher_priority_wins(self, mock_db):
        """When multiple routes match, the highest priority one wins."""
        low_priority = MagicMock()
        low_priority.enabled = True
        low_priority.decision_type = "create_task"
        low_priority.agent_pipeline = [{"agent": "observer"}]
        low_priority.conditions = None
        low_priority.priority = 50
        low_priority.weight = 1.0

        high_priority = MagicMock()
        high_priority.enabled = True
        high_priority.decision_type = "create_task"
        high_priority.agent_pipeline = [{"agent": "governor"}]
        high_priority.conditions = None
        high_priority.priority = 200
        high_priority.weight = 1.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [low_priority, high_priority]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        pipeline = await resolver.resolve({"decision": "create_task"})

        assert pipeline[0]["agent"] == "governor"

    @pytest.mark.asyncio
    async def test_weight_breaks_priority_tie(self, mock_db):
        """When priorities are equal, higher weight wins."""
        low_weight = MagicMock()
        low_weight.enabled = True
        low_weight.decision_type = "research"
        low_weight.agent_pipeline = [{"agent": "librarian"}]
        low_weight.conditions = None
        low_weight.priority = 100
        low_weight.weight = 0.5

        high_weight = MagicMock()
        high_weight.enabled = True
        high_weight.decision_type = "research"
        high_weight.agent_pipeline = [{"agent": "researcher"}]
        high_weight.conditions = None
        high_weight.priority = 100
        high_weight.weight = 2.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [low_weight, high_weight]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        pipeline = await resolver.resolve({"decision": "research"})

        assert pipeline[0]["agent"] == "researcher"


# ── Caching ────────────────────────────────────────────────────


class TestRouteCache:
    @pytest.mark.asyncio
    async def test_cache_avoids_repeated_queries(self, mock_db):
        """Routes should be cached after first query."""
        route = MagicMock()
        route.enabled = True
        route.decision_type = "acknowledge"
        route.agent_pipeline = []
        route.conditions = None
        route.priority = 10
        route.weight = 1.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [route]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        await resolver.resolve({"decision": "acknowledge"})
        await resolver.resolve({"decision": "acknowledge"})

        # Only one DB query (cached after first)
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_cache(self, mock_db):
        """invalidate_cache should force fresh query."""
        route = MagicMock()
        route.enabled = True
        route.decision_type = "acknowledge"
        route.agent_pipeline = []
        route.conditions = None
        route.priority = 10
        route.weight = 1.0

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [route]
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        await resolver.resolve({"decision": "acknowledge"})
        resolver.invalidate_cache()
        await resolver.resolve({"decision": "acknowledge"})

        assert mock_db.execute.call_count == 2


# ── Orchestrator Step Condition ────────────────────────────────


class TestOrchestratorStepCondition:
    def test_check_step_condition_has_key(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        assert JarvisOrchestrator._check_step_condition({"has_key": "plan_id"}, {"plan_id": "p1"})
        assert not JarvisOrchestrator._check_step_condition(
            {"has_key": "plan_id"}, {"other": "val"}
        )

    def test_check_step_condition_not_has_key(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        assert JarvisOrchestrator._check_step_condition({"not_has_key": "error"}, {"plan_id": "p1"})
        assert not JarvisOrchestrator._check_step_condition(
            {"not_has_key": "error"}, {"error": "oops"}
        )

    def test_check_step_condition_value_match(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        assert JarvisOrchestrator._check_step_condition(
            {"decision": "create_task"}, {"decision": "create_task"}
        )
        assert not JarvisOrchestrator._check_step_condition(
            {"decision": "create_task"}, {"decision": "research"}
        )


# ── CRUD Operations ────────────────────────────────────────────


class TestRouteCRUD:
    @pytest.mark.asyncio
    async def test_create_route(self, mock_db):
        """Should create a route with given parameters."""
        resolver = RouteResolver(mock_db)
        await resolver.create_route(
            name="custom_route",
            decision_type="custom",
            agent_pipeline=[{"agent": "researcher"}],
            priority=150,
        )

        assert mock_db.add.called
        added = mock_db.add.call_args[0][0]
        assert added.route_id.startswith("rte_")
        assert added.name == "custom_route"
        assert added.decision_type == "custom"
        assert added.priority == 150

    @pytest.mark.asyncio
    async def test_update_route(self, mock_db):
        """Should update allowed fields."""
        existing = MagicMock()
        existing.route_id = "rte_test"
        existing.name = "old_name"
        existing.priority = 100

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        updated = await resolver.update_route("rte_test", {"name": "new_name", "priority": 200})

        assert updated is not None
        assert existing.name == "new_name"
        assert existing.priority == 200

    @pytest.mark.asyncio
    async def test_update_nonexistent_route(self, mock_db):
        """Should return None for nonexistent route."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        result = await resolver.update_route("rte_nonexist", {"name": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_route(self, mock_db):
        """Should delete an existing route."""
        existing = MagicMock()
        existing.route_id = "rte_test"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        deleted = await resolver.delete_route("rte_test")

        assert deleted is True
        mock_db.delete.assert_called_once_with(existing)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_route(self, mock_db):
        """Should return False for nonexistent route."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        resolver = RouteResolver(mock_db)
        deleted = await resolver.delete_route("rte_nonexist")
        assert deleted is False

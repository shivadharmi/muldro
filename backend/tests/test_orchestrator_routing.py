"""Tests for capability-based routing in process_message()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_orchestrator():
    """Create a JarvisOrchestrator with all deps mocked."""
    from src.orchestrator.jarvis import JarvisOrchestrator

    settings = MagicMock()
    settings.use_bedrock = False
    settings.daily_token_budget_usd = 10.0
    settings.redis_url = "redis://localhost:6379"

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=ctx)

    services = MagicMock()
    services.memory_service = AsyncMock()
    services.memory_service.store_goal_memory = AsyncMock(return_value="mem_1")
    services.memory_service.store_briefing_memory = AsyncMock(return_value="mem_2")
    services.redis = None
    services.world_model = None
    services.artifact_store = None
    services.graph_engine = None
    services.tri_search = None
    services.reranker = None
    services.notifier = None

    with patch("src.orchestrator.jarvis.get_anthropic_client"):
        orch = JarvisOrchestrator(settings=settings, db_factory=db_factory, services=services)

    return orch


class TestProcessMessageRouting:
    """process_message() uses PlanOutput capability-based routing."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_fast_path_greeting_routes_to_presenter(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()

        agents_called = []

        async def mock_call_agent(agent_name, **kwargs):
            agents_called.append(agent_name)
            return "Hello! How can I help?"

        orch._call_agent = mock_call_agent
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")
        orch._get_available_capabilities = AsyncMock(return_value=[])

        result = await orch.process_message(
            message="Hey Jarvis",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        # Fast path greeting -> intent_to_plan -> PlanStep(capability="respond")
        # -> Presenter agent
        assert "presenter" in agents_called
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_system_set_goal_calls_handler(self, mock_classify):
        """Planner returns a system.set_goal step -> direct handler called."""
        mock_classify.return_value = ("command", 0.9, [])
        orch = _make_orchestrator()

        plan_json = (
            '{"goal": "Launch by April", "steps": [{"step_id": "s1", '
            '"description": "Set goal", "capability": "system.set_goal"}], '
            '"achievable": "full"}'
        )

        async def mock_call_agent(agent_name, **kwargs):
            if agent_name == "planner":
                return plan_json
            return "Goal set!"

        orch._call_agent = mock_call_agent
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        result = await orch.process_message(
            message="I want to launch the product by April",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        orch._services.memory_service.store_goal_memory.assert_called_once()
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_no_resolve_pipeline_called(self, mock_classify):
        """_resolve_pipeline should NOT be called in the new routing."""
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()
        orch._resolve_pipeline = AsyncMock(side_effect=AssertionError("Should not be called"))

        async def mock_call_agent(agent_name, **kwargs):
            return "Hi!"

        orch._call_agent = mock_call_agent
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")
        orch._get_available_capabilities = AsyncMock(return_value=[])

        result = await orch.process_message(message="Hi", user_id="usr_1", workspace_id="ws_1")
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_uses_extract_plan_not_extract_decision(self, mock_classify):
        """Planner path uses extract_plan, returns PlanOutput in result."""
        mock_classify.return_value = ("command", 0.9, [])
        orch = _make_orchestrator()

        plan_json = (
            '{"goal": "Check email", "steps": [{"step_id": "s1", '
            '"description": "Read emails", "capability": "respond"}], '
            '"achievable": "full"}'
        )

        async def mock_call_agent(agent_name, **kwargs):
            if agent_name == "planner":
                return plan_json
            return "Here are your emails."

        orch._call_agent = mock_call_agent
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        result = await orch.process_message(
            message="Check my email", user_id="usr_1", workspace_id="ws_1"
        )
        # Result should contain "plan" key (not "decision" key from old routing)
        assert "plan" in result
        assert "error" not in result


class TestProcessMessageStreamRouting:
    """process_message_stream() uses PlanOutput capability-based routing."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_stream_fast_path_emits_plan_event(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()

        async def mock_call_agent_stream(agent_name, **kwargs):
            yield {"event": "agent_start", "agent": agent_name, "model": "sonnet"}
            yield {"event": "agent_done", "agent": agent_name, "text": "Hi!"}

        orch._call_agent_stream = mock_call_agent_stream
        orch._call_agent = AsyncMock(return_value="")
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._spawn_background = MagicMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")
        orch._get_available_capabilities = AsyncMock(return_value=[])

        events = []
        async for evt in orch.process_message_stream(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        ):
            events.append(evt)

        event_types = [e.get("event") for e in events]
        assert "plan" in event_types
        assert "done" in event_types
        # Should NOT have old "decision" event
        assert "decision" not in event_types

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_stream_does_not_call_resolve_pipeline(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()
        orch._resolve_pipeline = AsyncMock(side_effect=AssertionError("Should not be called"))

        async def mock_call_agent_stream(agent_name, **kwargs):
            yield {"event": "agent_done", "agent": agent_name, "text": "Hi!"}

        orch._call_agent_stream = mock_call_agent_stream
        orch._call_agent = AsyncMock(return_value="")
        orch._log_interaction = AsyncMock(return_value="ilog_01")
        orch._push_workspace_surface = AsyncMock()
        orch._spawn_background = MagicMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")
        orch._get_available_capabilities = AsyncMock(return_value=[])

        events = []
        async for evt in orch.process_message_stream(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        ):
            events.append(evt)
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 0


class TestSurfacePushForPlanOutput:
    """_derive_surface_kind works with PlanOutput."""

    def test_respond_only_returns_none(self):
        from src.orchestrator.contracts import PlanOutput, PlanStep
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(
            goal="Hi",
            steps=[
                PlanStep(step_id="s1", description="Respond", capability="respond"),
            ],
        )
        assert _derive_surface_kind(plan) is None

    def test_write_action_returns_plan(self):
        from src.orchestrator.contracts import PlanOutput, PlanStep
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(
            goal="Send email",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Read",
                    capability="email.read",
                    risk="none",
                ),
                PlanStep(
                    step_id="s2",
                    description="Draft",
                    capability="email.draft",
                    risk="medium",
                ),
            ],
        )
        kind, title = _derive_surface_kind(plan)
        assert kind == "plan"

    def test_briefing_capability(self):
        from src.orchestrator.contracts import PlanOutput, PlanStep
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(
            goal="Add to brief",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Add",
                    capability="system.add_to_brief",
                ),
            ],
        )
        kind, title = _derive_surface_kind(plan)
        assert kind == "briefing"

    def test_single_read_returns_summary(self):
        from src.orchestrator.contracts import PlanOutput, PlanStep
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(
            goal="Check email",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Read",
                    capability="email.search",
                    risk="none",
                ),
            ],
        )
        kind, title = _derive_surface_kind(plan)
        assert kind == "summary"

    def test_empty_steps_returns_none(self):
        from src.orchestrator.contracts import PlanOutput
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(goal="Nothing", steps=[])
        assert _derive_surface_kind(plan) is None

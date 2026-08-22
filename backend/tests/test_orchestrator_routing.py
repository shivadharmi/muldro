"""Tests for capability-based planning in process_message().

The per-step routing assertions that used to live here ("greeting routes to the
Presenter", "a single read skips the Presenter") went with the legacy multi-agent arm:
there is no per-step agent call left to route, and no Presenter step to skip. What the
Planner still decides is the plan — its steps, their capabilities, and the ``system.*``
steps that run deterministically before the lead. That is what these pin.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


def _make_orchestrator(*, lead_text: str = "Hello! How can I help?"):
    """Create a MuldroOrchestrator with all deps mocked.

    ``make_mock_settings`` (NOT a bare ``MagicMock``) is load-bearing: an unset MagicMock
    attribute is TRUTHY, so a bare mock silently flips ``chat_planless`` on and drops the
    Planner these tests are about.
    """
    from src.orchestrator.muldro import MuldroOrchestrator

    settings = make_mock_settings(daily_token_budget_usd=10.0)

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

    orch = MuldroOrchestrator(settings=settings, db_factory=db_factory, services=services)

    # The turn's ONE lead. Recorded on the orchestrator so tests can read the reply back.
    async def _stream_deep_lead(lead, tools=None, **kw):
        yield {"event": "agent_start", "agent": "lead", "model": "m"}
        yield {"event": "agent_done", "agent": "lead", "text": lead_text}

    orch._chat._invoker.build_chat_lead = AsyncMock(return_value=MagicMock(name="lead"))
    orch._chat._invoker.build_planless_lead = AsyncMock(return_value=MagicMock(name="lead"))
    orch._chat._invoker.stream_deep_lead = _stream_deep_lead
    orch._chat._invoker.has_durable_checkpointer = MagicMock(return_value=True)
    orch._chat._plans.log_interaction = AsyncMock(return_value="ilog_01")
    orch._chat._events.emit_runtime_event = AsyncMock()
    orch._chat._context.load_conversation_history = AsyncMock(return_value="")
    orch._chat._context.assemble_context = AsyncMock(return_value="")
    orch._chat._get_available_capabilities = AsyncMock(return_value=[])
    return orch


class TestProcessMessagePlanning:
    """``process_message()`` plans with PlanOutput, then runs ONE lead."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.chat_processor.classify_intent")
    async def test_greeting_still_replies_with_no_per_step_agent(self, mock_classify):
        """A greeting reaches a reply, and the Planner is the ONLY per-agent call left —
        the answer comes from the lead, not from a routed Presenter step."""
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator(lead_text="Hello! How can I help?")

        agents_called = []

        async def mock_call_agent_stream(agent_name, **kwargs):
            agents_called.append(agent_name)
            yield {"event": "agent_done", "agent": agent_name, "text": ""}

        orch._chat._invoker.call_agent_stream = mock_call_agent_stream

        result = await orch.process_message(
            message="Hey Muldro",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        # The batch entry defaults to mode="plan", which forces the Planner regardless of
        # intent. It is the only named agent invoked: no presenter, no perceiver.
        assert agents_called == ["planner"]
        assert result["presentation"] == "Hello! How can I help?"
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.chat_processor.classify_intent")
    async def test_system_set_goal_calls_handler(self, mock_classify):
        """Planner returns a system.set_goal step -> the handler runs deterministically,
        ahead of the lead."""
        mock_classify.return_value = ("command", 0.9, [])
        orch = _make_orchestrator(lead_text="Goal set!")

        plan_json = (
            '{"goal": "Launch by April", "steps": [{"step_id": "s1", '
            '"description": "Set goal", "capability": "system.set_goal"}], '
            '"achievable": "full"}'
        )

        async def mock_call_agent_stream(agent_name, **kwargs):
            text = plan_json if agent_name == "planner" else ""
            yield {"event": "agent_done", "agent": agent_name, "text": text}

        orch._chat._invoker.call_agent_stream = mock_call_agent_stream

        result = await orch.process_message(
            message="I want to launch the product by April",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        orch._services.memory_service.store_goal_memory.assert_called_once()
        assert result["system_system.set_goal"] is not None
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.chat_processor.classify_intent")
    async def test_uses_extract_plan_not_extract_decision(self, mock_classify):
        """Planner path uses extract_plan, returns PlanOutput in result."""
        mock_classify.return_value = ("command", 0.9, [])
        orch = _make_orchestrator(lead_text="Here are your emails.")

        plan_json = (
            '{"goal": "Check email", "steps": [{"step_id": "s1", '
            '"description": "Read emails", "capability": "respond"}], '
            '"achievable": "full"}'
        )

        async def mock_call_agent_stream(agent_name, **kwargs):
            text = plan_json if agent_name == "planner" else ""
            yield {"event": "agent_done", "agent": agent_name, "text": text}

        orch._chat._invoker.call_agent_stream = mock_call_agent_stream

        result = await orch.process_message(
            message="Check my email", user_id="usr_1", workspace_id="ws_1"
        )
        # Result should contain "plan" key (not "decision" key from old routing)
        assert "plan" in result
        assert result["plan"]["goal"] == "Check email"
        assert "error" not in result


class TestProcessMessageStreamPlanning:
    """``process_message_stream()`` yields the same plan, as SSE frames."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.chat_processor.classify_intent")
    async def test_stream_fast_path_emits_plan_event(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator(lead_text="Hi!")
        orch._chat._spawn_background = MagicMock()

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
    @patch("src.orchestrator.chat_processor.classify_intent")
    async def test_stream_reaches_a_response_without_error(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator(lead_text="Hi!")
        orch._chat._spawn_background = MagicMock()

        events = []
        async for evt in orch.process_message_stream(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        ):
            events.append(evt)

        assert [e for e in events if e.get("event") == "error"] == []
        responses = [e["text"] for e in events if e.get("event") == "response"]
        assert responses == ["Hi!"]


class TestSurfacePushForPlanOutput:
    """_derive_surface_kind works with PlanOutput."""

    def test_respond_only_returns_none(self):
        from src.contracts import PlanOutput, PlanStep
        from src.services.surface_mapping import derive_surface_kind

        plan = PlanOutput(
            goal="Hi",
            steps=[
                PlanStep(step_id="s1", description="Respond", capability="respond"),
            ],
        )
        assert derive_surface_kind(plan) is None

    def test_write_action_returns_plan(self):
        from src.contracts import PlanOutput, PlanStep
        from src.services.surface_mapping import derive_surface_kind

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
        kind, title = derive_surface_kind(plan)
        assert kind == "plan"

    def test_briefing_capability(self):
        from src.contracts import PlanOutput, PlanStep
        from src.services.surface_mapping import derive_surface_kind

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
        kind, title = derive_surface_kind(plan)
        assert kind == "briefing"

    def test_single_read_returns_summary(self):
        from src.contracts import PlanOutput, PlanStep
        from src.services.surface_mapping import derive_surface_kind

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
        kind, title = derive_surface_kind(plan)
        assert kind == "summary"

    def test_empty_steps_returns_none(self):
        from src.contracts import PlanOutput
        from src.services.surface_mapping import derive_surface_kind

        plan = PlanOutput(goal="Nothing", steps=[])
        assert derive_surface_kind(plan) is None

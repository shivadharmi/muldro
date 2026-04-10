"""Tests for the Jarvis orchestrator module."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

# ── Tracing Tests ────────────────────────────────────────────────────────


class TestTracing:
    def test_trace_creation(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("user_message")
        assert trace.trace_id.startswith("trace_")
        assert trace.trigger == "user_message"
        assert trace.started_at is not None
        assert trace.ended_at is None

    def test_span_lifecycle(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("test")

        span = trace.start_span("planner")
        assert span.span_id.startswith("span_")
        assert span.agent_name == "planner"
        assert span.started_at is not None

        trace.end_span(
            span.span_id,
            input_tokens=100,
            output_tokens=50,
            tools_called=["search"],
            decision="create_task",
        )
        assert span.ended_at is not None
        assert span.input_tokens == 100
        assert span.output_tokens == 50
        assert span.tools_called == ["search"]
        assert span.decision == "create_task"
        assert span.duration_ms() >= 0

    def test_trace_total_tokens(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("test")

        s1 = trace.start_span("observer")
        trace.end_span(s1.span_id, input_tokens=500, output_tokens=100)

        s2 = trace.start_span("planner")
        trace.end_span(s2.span_id, input_tokens=1000, output_tokens=300)

        input_t, output_t = trace.total_tokens()
        assert input_t == 1500
        assert output_t == 400

    def test_trace_finish_closes_active_spans(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("test")
        span = trace.start_span("observer")

        trace.finish()
        assert trace.ended_at is not None
        assert span.ended_at is not None
        assert span.error == "trace_finished_with_active_span"

    def test_trace_to_dict(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("test")
        span = trace.start_span("observer")
        trace.end_span(span.span_id, input_tokens=100, output_tokens=50)
        trace.finish()

        d = trace.to_dict()
        assert d["trace_id"] == trace.trace_id
        assert d["trigger"] == "test"
        assert len(d["spans"]) == 1
        assert d["spans"][0]["agent_name"] == "observer"
        assert d["total_input_tokens"] == 100

    @pytest.mark.asyncio
    async def test_finish_trace_removes_from_active(self):
        from src.orchestrator.tracing import TraceManager

        manager = TraceManager()
        trace = manager.start_trace("test")
        assert manager.get_trace(trace.trace_id) is not None

        await manager.finish_trace(
            trace.trace_id, user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
        )
        assert manager.get_trace(trace.trace_id) is None


# ── Budget Tests ─────────────────────────────────────────────────────────


class TestBudget:
    def test_cost_calculation_sonnet(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=5.0)
        cost = tracker.calculate_cost(
            "claude-sonnet-4-20250514", input_tokens=1_000_000, output_tokens=0
        )
        assert cost == pytest.approx(3.0)

    def test_cost_calculation_opus(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=5.0)
        cost = tracker.calculate_cost(
            "claude-opus-4-20250514", input_tokens=0, output_tokens=1_000_000
        )
        assert cost == pytest.approx(75.0)

    def test_cost_calculation_unknown_model_defaults_sonnet(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker()
        cost = tracker.calculate_cost("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(3.0)  # Sonnet pricing

    def test_budget_status_normal(self):
        from src.orchestrator.budget import BudgetStatus

        status = BudgetStatus(
            daily_spend_usd=1.0,
            daily_limit_usd=5.0,
            budget_mode="normal",
            remaining_usd=4.0,
            percent_used=20.0,
        )
        assert status.budget_mode == "normal"

    def test_should_allow_perception(self):
        from src.orchestrator.budget import BudgetStatus, BudgetTracker

        tracker = BudgetTracker()
        normal = BudgetStatus(
            daily_spend_usd=1.0,
            daily_limit_usd=5.0,
            budget_mode="normal",
            remaining_usd=4.0,
            percent_used=20.0,
        )
        paused = BudgetStatus(
            daily_spend_usd=4.8,
            daily_limit_usd=5.0,
            budget_mode="paused",
            remaining_usd=0.2,
            percent_used=96.0,
        )
        assert tracker.should_allow_perception(normal) is True
        assert tracker.should_allow_perception(paused) is False

    def test_interval_multiplier(self):
        from src.orchestrator.budget import BudgetStatus, BudgetTracker

        tracker = BudgetTracker()
        degraded = BudgetStatus(
            daily_spend_usd=4.2,
            daily_limit_usd=5.0,
            budget_mode="degraded",
            remaining_usd=0.8,
            percent_used=84.0,
        )
        assert tracker.get_perception_interval_multiplier(degraded) == 3

    def test_cycle_budget_check(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker()
        assert tracker.check_cycle_budget(10_000) is True
        assert tracker.check_cycle_budget(60_000) is False


# ── Agent Definition Tests ───────────────────────────────────────────────


class TestAgents:
    def test_all_agents_defined(self):
        from src.orchestrator.agents import AGENTS

        expected = {
            "observer",
            "librarian",
            "planner",
            "governor",
            "operator",
            "presenter",
            "researcher",
            "persona",
        }
        assert set(AGENTS.keys()) == expected

    def test_planner_uses_opus(self):
        from src.orchestrator.agents import AGENTS

        assert AGENTS["planner"].model_tier == "opus"

    def test_persona_uses_haiku(self):
        from src.orchestrator.agents import AGENTS

        assert AGENTS["persona"].model_tier == "haiku"

    @pytest.mark.asyncio
    async def test_tool_scoping(self):
        from unittest.mock import AsyncMock, MagicMock

        from src.orchestrator.agents import AGENTS

        mock_db = AsyncMock()

        # Helper to mock tool registry
        def make_tool(name, capability):
            tool = MagicMock()
            tool.name = name
            tool.capability = capability
            return tool

        # Mock registry
        from unittest.mock import patch

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            mock_reg.get_tool = AsyncMock(
                side_effect=lambda name: {
                    "ingest_event": make_tool("ingest_event", "internal.ingest_event"),
                    "gmail_send": make_tool(
                        "gmail_send", "email.send"
                    ),  # email.send, not gmail.send
                    "get_active_plans": make_tool("get_active_plans", "internal.get_active_plans"),
                    "search": make_tool("search", "internal.search"),
                    "slack_post_message": make_tool(
                        "slack_post_message", "messaging.send"
                    ),  # messaging.send
                }.get(name)
            )
            mock_reg_cls.return_value = mock_reg

            # Observer can ingest events but not send email
            assert await AGENTS["observer"].can_use_tool("ingest_event", mock_db) is True
            assert await AGENTS["observer"].can_use_tool("gmail_send", mock_db) is False

            # Operator can send email but not plan
            assert await AGENTS["operator"].can_use_tool("gmail_send", mock_db) is True
            assert await AGENTS["operator"].can_use_tool("get_active_plans", mock_db) is False

            # Researcher is read-only (no write tools)
            assert await AGENTS["researcher"].can_use_tool("search", mock_db) is True
            assert await AGENTS["researcher"].can_use_tool("gmail_send", mock_db) is False
            assert await AGENTS["researcher"].can_use_tool("slack_post_message", mock_db) is False

    def test_planner_has_higher_max_tokens(self):
        from src.orchestrator.agents import AGENTS

        assert AGENTS["planner"].max_tokens == 8192
        assert AGENTS["observer"].max_tokens == 4096

    def test_governor_has_low_temperature(self):
        from src.orchestrator.agents import AGENTS

        assert AGENTS["governor"].temperature == 0.1


# ── Hooks Tests ──────────────────────────────────────────────────────────


class TestHooks:
    async def test_read_only_tools_allowed(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        result = await governor_pre_tool_hook("search", {}, "planner", user_id=TEST_USER_ID)
        assert result["allowed"] is True

    async def test_write_tools_rejected_via_catalog(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        # linear_delete_issue is critical in catalog — requires approval
        result = await governor_pre_tool_hook(
            "linear_delete_issue", {}, "operator", user_id=TEST_USER_ID
        )
        assert result["allowed"] is False

    async def test_write_tools_require_approval(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        result = await governor_pre_tool_hook(
            "send_gmail_message", {}, "operator", user_id=TEST_USER_ID
        )
        assert result["allowed"] is False
        assert result["approval_required"] is True

    async def test_internal_tools_allowed(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        result = await governor_pre_tool_hook("ingest_event", {}, "observer", user_id=TEST_USER_ID)
        assert result["allowed"] is True

    async def test_audit_hook_logs(self):
        from src.orchestrator.hooks import audit_post_tool_hook

        # Should not raise even without db_factory
        await audit_post_tool_hook(
            "search_memory",
            {"query": "test"},
            {"results": []},
            "planner",
            trace_id="trace_123",
        )


# ── Prompts Tests ────────────────────────────────────────────────────────


class TestPrompts:
    def test_all_prompts_defined(self):
        from src.orchestrator.prompts import AGENT_PROMPTS

        expected = {
            "observer",
            "librarian",
            "planner",
            "governor",
            "operator",
            "presenter",
            "researcher",
            "persona",
        }
        assert set(AGENT_PROMPTS.keys()) == expected

    def test_jarvis_soul_not_empty(self):
        from src.orchestrator.prompts import JARVIS_SOUL

        assert len(JARVIS_SOUL) > 100
        assert "operating system" in JARVIS_SOUL.lower()

    def test_planner_prompt_mentions_json(self):
        from src.orchestrator.prompts import PLANNER_PROMPT

        assert "JSON" in PLANNER_PROMPT

    def test_governor_prompt_mentions_approval(self):
        from src.orchestrator.prompts import GOVERNOR_PROMPT

        assert "approval" in GOVERNOR_PROMPT.lower()


# ── Orchestrator Tests ───────────────────────────────────────────────────


class TestOrchestrator:
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_process_message_routes_to_planner(self, mock_get_client):
        from unittest.mock import AsyncMock

        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock Claude response (text only, no tool use)
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                type="text",
                text='{"decision": "acknowledge", "reasoning": "Noted."}',
            )
        ]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        settings = make_mock_settings(
            daily_token_budget_usd=5.0,
            use_bedrock=False,
            telegram_bot_token="",
        )

        # Build a mock db session where sync methods (add) are MagicMock
        # and async methods (flush, commit, etc.) are AsyncMock
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        # db_factory() returns an async context manager yielding mock_db
        db_ctx = AsyncMock()
        db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        db_ctx.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=db_ctx)

        orchestrator = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )

        # Mock _get_tools_for_agent to avoid DB queries
        from unittest.mock import AsyncMock

        orchestrator._get_tools_for_agent = AsyncMock(return_value=[])

        result = await orchestrator.process_message(
            "What should I focus on?",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )
        # New capability-based routing returns "plan" instead of "decision"
        assert "plan" in result or "trace_id" in result
        assert result["trace_id"].startswith("trace_")

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_extract_plan_from_json(self, mock_get_client):
        from src.orchestrator.intent_classifier import extract_plan

        # Test JSON extraction — returns PlanOutput
        text = (
            "Here is my analysis:\n"
            '{"goal": "Create task", "steps": [{"description": "Do it", '
            '"capability": "respond"}], "priority": "high"}\nDone.'
        )
        result = extract_plan(text)
        assert result.goal == "Create task"
        assert result.priority == "high"

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_extract_plan_fallback(self, mock_get_client):
        from src.orchestrator.intent_classifier import extract_plan

        # No JSON in response — fallback to PlanOutput defaults
        result = extract_plan("Just some plain text response")
        assert result.steps[0].capability == "respond"


# ── Recovery Tests ───────────────────────────────────────────────────────


class TestRecovery:
    async def test_recovery_handles_empty_db(self):
        from unittest.mock import AsyncMock

        from src.orchestrator.recovery import run_startup_recovery

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        summary = await run_startup_recovery(mock_db)
        assert summary["orphaned_plans"] == 0
        assert summary["stale_task_runs"] == 0
        assert summary["expired_approvals"] == 0


# ── A2UI Renderer Tests ─────────────────────────────────────────────────


class TestA2UIRenderer:
    def test_text_component(self):
        from src.ui.renderer import text

        c = text("t1", "Hello", "heading")
        assert c.type == "Text"
        assert c.id == "t1"
        assert c.properties["text"] == "Hello"
        assert c.properties["variant"] == "heading"

    def test_button_with_action(self):
        from src.ui.renderer import button

        c = button("b1", "Click me", "primary", {"action": "approve", "id": "apr_1"})
        assert c.type == "Button"
        assert len(c.actions) == 1
        assert c.actions[0].payload["action"] == "approve"

    def test_card_with_children(self):
        from src.ui.renderer import card, text

        c = card("c1", [text("t1", "Title"), text("t2", "Body")])
        assert c.type == "Card"
        assert len(c.children) == 2

    def test_briefing_surface(self):
        from src.ui.renderer import briefing_surface

        s = briefing_surface(
            briefing_id="brief_001",
            headline="3 priorities, 1 approval pending",
            priorities=[
                {"title": "Reply to investor", "why": "Fundraising thread"},
            ],
            approvals=[
                {"approval_id": "apr_01", "title": "Send email", "risk_level": "high"},
            ],
            schedule=[
                {"time": "10:00 AM", "title": "Strategy Meeting", "event_id": "evt_42"},
            ],
        )
        assert s.type == "surface"
        assert s.id == "brief_001"
        # headline + priorities + approvals + schedule = 4 cards
        assert len(s.children) == 4

    def test_surface_serialization(self):
        from src.ui.renderer import surface, text

        s = surface("test", [text("t1", "Hello")])
        d = s.model_dump()
        assert d["type"] == "surface"
        assert d["id"] == "test"
        assert len(d["children"]) == 1
        assert d["children"][0]["type"] == "Text"


# ── Perception Coordinator Tests ─────────────────────────────────────────


class TestPerception:
    def test_policy_service_effective_interval(self):
        """Policy service computes effective interval from base + backoff."""
        from src.models.perception_state import PerceptionState
        from src.services.perception_policy import PerceptionPolicyService

        state = PerceptionState(
            state_id="pst_test",
            workspace_id="ws_test",
            user_id=TEST_USER_ID,
            source="gmail",
            mode="poll",
            base_interval_s=300,
            effective_interval_s=300,
            consecutive_failures=0,
        )
        svc = PerceptionPolicyService(AsyncMock())
        assert svc._compute_effective_interval(state) == 300

    def test_policy_service_backoff(self):
        """Failures double the effective interval."""
        from src.models.perception_state import PerceptionState
        from src.services.perception_policy import PerceptionPolicyService

        state = PerceptionState(
            state_id="pst_test",
            workspace_id="ws_test",
            user_id=TEST_USER_ID,
            source="gmail",
            mode="poll",
            base_interval_s=300,
            effective_interval_s=300,
            consecutive_failures=1,
        )
        svc = PerceptionPolicyService(AsyncMock())
        assert svc._compute_effective_interval(state) == 600

    def test_policy_service_budget_multiplier(self):
        """Budget multiplier stretches next_run_at."""
        from src.models.perception_state import PerceptionState
        from src.services.perception_policy import PerceptionPolicyService

        state = PerceptionState(
            state_id="pst_test",
            workspace_id="ws_test",
            user_id=TEST_USER_ID,
            source="gmail",
            mode="poll",
            base_interval_s=300,
            effective_interval_s=300,
            last_run_at=datetime.now(timezone.utc),
        )
        svc = PerceptionPolicyService(AsyncMock())
        next_run = svc._compute_next_run(state, budget_multiplier=3)
        delta = (next_run - datetime.now(timezone.utc)).total_seconds()
        # 300 * 3 = 900s
        assert 899 <= delta <= 901

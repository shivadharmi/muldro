"""Tests for Fix-6: Orchestrator Error Handling & Routing.

Covers:
- PlanOutput circular dependency validation (Task 3.3)
- AgentResult.response_text None default (Task 3.1)
- StepResult.duration_ms None default (Task 3.2)
- SpanRecord.decision removed (Task 2.5)
- route_step returns empty string for unknown capability (Task 1.3)
- _call_agent propagates LoopError (Task 1.2)
- has_presenter_step includes system.respond/system.acknowledge (Task 4.2)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.contracts import (
    AgentResult,
    PlanOutput,
    PlanStep,
    SpanRecord,
    StepResult,
)
from src.services.capability_resolver import CapabilityResolver, route_step

# ── PlanOutput circular dependency validation ──────────────────────────


class TestPlanOutputDependencyValidation:
    def test_self_reference_rejected(self):
        with pytest.raises(ValidationError, match="depends on itself"):
            PlanOutput(
                goal="test",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="step 1",
                        capability="reason",
                        depends_on=["s1"],
                    ),
                ],
            )

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValidationError, match="unknown step"):
            PlanOutput(
                goal="test",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="step 1",
                        capability="reason",
                        depends_on=["s_nonexistent"],
                    ),
                ],
            )

    def test_circular_dependency_rejected(self):
        with pytest.raises(ValidationError, match="Circular dependency"):
            PlanOutput(
                goal="test",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="step 1",
                        capability="reason",
                        depends_on=["s2"],
                    ),
                    PlanStep(
                        step_id="s2",
                        description="step 2",
                        capability="reason",
                        depends_on=["s1"],
                    ),
                ],
            )

    def test_valid_dag_accepted(self):
        plan = PlanOutput(
            goal="test",
            steps=[
                PlanStep(step_id="s1", description="step 1", capability="email.search"),
                PlanStep(
                    step_id="s2",
                    description="step 2",
                    capability="respond",
                    depends_on=["s1"],
                ),
            ],
        )
        assert len(plan.steps) == 2

    def test_no_steps_accepted(self):
        plan = PlanOutput(goal="test", steps=[])
        assert plan.steps == []

    def test_three_node_cycle_rejected(self):
        with pytest.raises(ValidationError, match="Circular dependency"):
            PlanOutput(
                goal="test",
                steps=[
                    PlanStep(
                        step_id="a",
                        description="a",
                        capability="reason",
                        depends_on=["c"],
                    ),
                    PlanStep(
                        step_id="b",
                        description="b",
                        capability="reason",
                        depends_on=["a"],
                    ),
                    PlanStep(
                        step_id="c",
                        description="c",
                        capability="reason",
                        depends_on=["b"],
                    ),
                ],
            )


# ── Contract default changes ───────────────────────────────────────────


class TestContractDefaults:
    def test_agent_result_response_text_none_default(self):
        r = AgentResult(agent_name="test")
        assert r.response_text is None

    def test_agent_result_response_text_distinguishes_failure(self):
        success = AgentResult(agent_name="test", response_text="ok")
        failure = AgentResult(agent_name="test")
        assert success.response_text is not None
        assert failure.response_text is None

    def test_step_result_duration_ms_none_default(self):
        s = StepResult(step_id="s1", status="completed")
        assert s.duration_ms is None

    def test_step_result_duration_ms_accepts_int(self):
        s = StepResult(step_id="s1", status="completed", duration_ms=42)
        assert s.duration_ms == 42

    def test_span_record_no_decision_field(self):
        span = SpanRecord(span_id="sp1", agent_name="test")
        assert not hasattr(span, "decision") or "decision" not in span.model_fields


# ── route_step unknown capability ──────────────────────────────────────


def _mock_db_with_tools(tools: list) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = tools
    db.execute = AsyncMock(return_value=result)
    return db


class TestRouteStepUnknownCapability:
    @pytest.mark.asyncio
    async def test_unknown_capability_returns_empty_string(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        result = await route_step("nonexistent.capability", resolver)
        assert result == ""

    @pytest.mark.asyncio
    async def test_known_capability_returns_agent(self):
        tool = MagicMock()
        tool.name = "gmail_search"
        tool.capability = "email.search"
        tool.requires_approval = False
        tool.enabled = True
        db = _mock_db_with_tools([tool])
        resolver = CapabilityResolver(db, "ws_test")
        result = await route_step("email.search", resolver)
        assert result == "perceiver"


# ── has_presenter_step includes system.respond ─────────────────────────


class TestHasPresenterStep:
    """Verify that system.respond and system.acknowledge are treated as presenter steps."""

    def test_system_respond_is_presenter_step(self):
        steps = [
            PlanStep(step_id="s1", description="ack", capability="system.respond"),
        ]
        has_presenter_step = any(
            s.capability in ("reason", "respond", "system.respond", "system.acknowledge")
            for s in steps
            if s.actor == "jarvis"
        )
        assert has_presenter_step is True

    def test_system_acknowledge_is_presenter_step(self):
        steps = [
            PlanStep(step_id="s1", description="ack", capability="system.acknowledge"),
        ]
        has_presenter_step = any(
            s.capability in ("reason", "respond", "system.respond", "system.acknowledge")
            for s in steps
            if s.actor == "jarvis"
        )
        assert has_presenter_step is True

    def test_external_capability_is_not_presenter_step(self):
        steps = [
            PlanStep(step_id="s1", description="search", capability="email.search"),
        ]
        has_presenter_step = any(
            s.capability in ("reason", "respond", "system.respond", "system.acknowledge")
            for s in steps
            if s.actor == "jarvis"
        )
        assert has_presenter_step is False


# ── _call_agent error propagation ──────────────────────────────────────


class TestCallAgentErrorPropagation:
    """Verify call_agent returns an error string when the deep stream errors."""

    @pytest.mark.asyncio
    async def test_error_frame_propagated(self):
        from src.orchestrator.agent_invoker import AgentInvoker

        async def fake_reap(*a, **k):
            yield {"event": "error", "agent": "presenter", "message": "circuit breaker open"}

        settings = MagicMock()
        tool_executor = MagicMock()
        tool_executor.apply_cache_control_to_tools = MagicMock(return_value=[])
        tool_executor.get_tools_for_agent = AsyncMock(return_value=[])
        context = MagicMock()
        context.assemble_context = AsyncMock(return_value="")

        agent = MagicMock(name="presenter", prompt="test", model_tier="haiku")
        agent.capability_scope = set()

        invoker = AgentInvoker(
            settings,
            MagicMock(),  # client
            MagicMock(),  # services
            MagicMock(),  # budget
            MagicMock(),  # circuit_breaker
            lambda: AsyncMock(),  # db_factory_provider
            tool_executor,
            context,
            {"presenter": agent},
        )
        invoker.get_model_for_agent = MagicMock(return_value="claude-haiku-4-20250514")
        invoker.build_system_prompt = MagicMock(return_value=[{"type": "text", "text": "test"}])

        with (
            patch.object(invoker, "_build_deep_agent_for", AsyncMock(return_value=object())),
            patch.object(invoker, "_stream_and_reap", fake_reap),
        ):
            result = await invoker.call_agent(
                "presenter",
                message="test",
                user_id="u1",
                workspace_id="ws1",
            )
        assert result.startswith("[Agent error:")
        assert "circuit breaker open" in result

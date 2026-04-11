"""Tests for GraphExecutor._emit_surface_update() and phase transitions."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.contracts import (
    ApprovalContext,
    ResultSummary,
    StepState,
)
from src.services.graph_executor import GraphExecutor


def _make_executor(redis_mock=None) -> GraphExecutor:
    settings = MagicMock()
    settings.redis_url = "redis://localhost"
    settings.resolved_model = "claude-sonnet-4-6-20250514"
    db = AsyncMock()
    executor = GraphExecutor(settings=settings, db=db)
    executor._redis = redis_mock
    return executor


class TestEmitSurfaceUpdate:
    @pytest.mark.asyncio
    async def test_no_op_when_no_surface_id(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        await executor._emit_surface_update(
            surface_id=None,
            user_id="usr_01",
            phase="plan_ready",
        )
        redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_plan_ready(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        steps = [
            StepState(step_id="s1", description="Search emails", status="pending"),
            StepState(step_id="s2", description="Draft reply", status="pending"),
        ]
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="plan_ready",
            steps=steps,
            progress="0/2 steps",
        )
        redis.publish.assert_called_once()
        channel, payload = redis.publish.call_args.args
        assert channel == "jarvis:a2ui:usr_01"
        data = json.loads(payload)
        assert data["type"] == "surface_update"
        assert data["surface_id"] == "surf_abc"
        assert data["phase"] == "plan_ready"
        assert len(data["steps"]) == 2

    @pytest.mark.asyncio
    async def test_publishes_executing_with_current_step(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
            steps=[StepState(step_id="s1", description="Search", status="executing")],
            current_step="s1",
        )
        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "executing"
        assert data["current_step"] == "s1"

    @pytest.mark.asyncio
    async def test_publishes_approval_needed(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        approval = ApprovalContext(
            approval_id="apr_01",
            step_description="Send email",
            risk_reasoning="External write",
            trust_context="First use",
            graduation_hint="9 more to auto-approve",
        )
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="approval_needed",
            approval=approval,
        )
        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "approval_needed"
        assert data["approval"]["approval_id"] == "apr_01"

    @pytest.mark.asyncio
    async def test_publishes_completed_with_results(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        results = ResultSummary(
            key_findings=["Found 3 emails"],
            artifacts_created=["draft_01"],
            suggested_next=["Review draft"],
        )
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="completed",
            results=results,
        )
        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "completed"
        assert data["results"]["key_findings"] == ["Found 3 emails"]

    @pytest.mark.asyncio
    async def test_publishes_failed(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="failed",
            progress="Failed at step 2/3",
        )
        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "failed"

    @pytest.mark.asyncio
    async def test_redis_failure_is_silent(self):
        redis = AsyncMock()
        redis.publish.side_effect = ConnectionError("Redis down")
        executor = _make_executor(redis_mock=redis)
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
        )
        # Should not raise

    @pytest.mark.asyncio
    async def test_falls_back_to_event_bus(self):
        """When no redis, should try event_bus.publish_to_channel."""
        event_bus = AsyncMock()
        executor = _make_executor(redis_mock=None)
        executor._event_bus = event_bus
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
        )
        event_bus.publish_to_channel.assert_called_once()
        channel = event_bus.publish_to_channel.call_args.args[0]
        assert channel == "jarvis:a2ui:usr_01"

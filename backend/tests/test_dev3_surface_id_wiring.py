"""Tests for DEV-3 and DEV-4 spec compliance gaps.

DEV-3: _push_workspace_surface returns surface_id so callers can wire it
       to execute_run / SSE done events.
DEV-4: GraphExecutor emits plan_ready phase after steps are populated,
       before execution begins.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts import StepState
from src.services.graph_executor import GraphExecutor

# ── Helpers ──────────────────────────────────────────────────────


def _make_executor(redis_mock=None) -> GraphExecutor:
    settings = MagicMock()
    settings.redis_url = "redis://localhost"
    settings.resolved_model = "claude-sonnet-4-6-20250514"
    db = AsyncMock()
    executor = GraphExecutor(settings=settings, db=db, redis=redis_mock)
    return executor


def _make_step(step_id="step_01", status="pending", task_id="t1", input_data=None):
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.status = status
    step.name = None
    step.input_data = input_data or {"capability": "email.send"}
    step.output_data = None
    step.depends_on = None
    step.started_at = None
    step.completed_at = None
    step.retry_count = 0
    step.max_retries = 1
    step.error = None
    return step


def _make_run(run_id="run_01", user_id="usr_01", workspace_id="ws_01", checkpoint=None):
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.workspace_id = workspace_id
    run.plan_id = "plan_01"
    run.source = "plan"
    run.status = "pending"
    run.started_at = None
    run.completed_at = None
    run.timeout_seconds = None
    run.trace_id = None
    run.checkpoint = checkpoint
    run.error = None
    return run


# ── DEV-3: _push_workspace_surface returns surface_id ────────────


class TestPushWorkspaceSurfaceReturnsSurfaceId:
    """_push_workspace_surface must return the generated surface_id."""

    @pytest.mark.asyncio
    async def test_returns_surface_id_on_success(self):
        """When surface kind is derivable, method returns surf_xxx string."""
        from src.contracts import PlanOutput, PlanStep
        from src.orchestrator.surface_pusher import SurfacePusher

        # Mock event bus with Redis that returns integer from incr (rate limit check)
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_event_bus = AsyncMock()
        mock_event_bus._redis = mock_redis
        events = MagicMock()
        events.ensure_event_bus = AsyncMock(return_value=mock_event_bus)

        # Mock DB persistence (inner context manager)
        db_factory = MagicMock()
        mock_db = AsyncMock()
        db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        sp = SurfacePusher(events, lambda: db_factory)

        plan = PlanOutput(
            goal="Send email",
            reasoning="Draft and send",
            steps=[PlanStep(description="Send email", capability="email.send")],
        )

        result = await sp.push_workspace_surface(
            plan=plan,
            user_id="usr_01",
            workspace_id="ws_01",
            run_id="run_01",
        )

        assert result is not None
        assert result.startswith("surf_")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_mapping(self):
        """When plan has no visual surface kind, returns None."""
        from src.contracts import PlanOutput, PlanStep
        from src.orchestrator.surface_pusher import SurfacePusher

        sp = SurfacePusher(MagicMock(), lambda: MagicMock())

        plan = PlanOutput(
            goal="Hello",
            reasoning="greeting",
            steps=[PlanStep(description="Greet", capability="respond")],
        )

        with patch("src.orchestrator.surface_pusher.derive_surface_kind", return_value=None):
            result = await sp.push_workspace_surface(
                plan=plan,
                user_id="usr_01",
                workspace_id="ws_01",
            )

        assert result is None


# ── DEV-4: plan_ready emission in execute_run ─────────────────


class TestPlanReadyEmission:
    """execute_run must emit plan_ready phase after loading steps."""

    @pytest.mark.asyncio
    async def test_plan_ready_emitted_before_dag_execution(self):
        """When surface_id is provided, plan_ready is emitted with step list."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        run = _make_run(checkpoint=None)
        steps = [
            _make_step(step_id="s1", input_data={"capability": "email.search"}),
            _make_step(step_id="s2", input_data={"capability": "email.send"}),
        ]

        # Mock DB query for run lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)

        # Mock _get_all_steps to return our test steps
        executor._get_all_steps = AsyncMock(return_value=steps)

        # Track _emit_surface_update calls
        emit_calls = []

        async def tracking_emit(**kwargs):
            emit_calls.append(kwargs)

        executor._emit_surface_update = AsyncMock(side_effect=tracking_emit)

        # Mock _execute_dag to do nothing (we only care about pre-dag behavior)
        executor._execute_dag = AsyncMock()
        executor._audit = AsyncMock()
        executor._emit_event = AsyncMock()

        await executor.execute_run("run_01", trace_id="t1", surface_id="surf_abc")

        # Verify plan_ready was emitted
        plan_ready_calls = [c for c in emit_calls if c.get("phase") == "plan_ready"]
        assert len(plan_ready_calls) == 1
        call = plan_ready_calls[0]
        assert call["surface_id"] == "surf_abc"
        assert call["user_id"] == "usr_01"
        assert len(call["steps"]) == 2
        # Steps should be StepState instances
        for s in call["steps"]:
            assert isinstance(s, StepState)
            assert s.status == "pending"

    @pytest.mark.asyncio
    async def test_plan_ready_defaults_to_run_surface_id_when_none_provided(self):
        """When no surface_id is provided, execute_run defaults to the run_id.

        The unified surface architecture always maintains a surface per run so
        the REST poll and WebSocket push target the same id; omitting the
        argument should therefore default to the canonical run surface id (the
        run_id itself, not a re-prefixed ``run_run_…``), not skip emission.
        """
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        run = _make_run()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)

        executor._emit_surface_update = AsyncMock()
        executor._execute_dag = AsyncMock()
        executor._audit = AsyncMock()
        executor._emit_event = AsyncMock()

        await executor.execute_run("run_01", trace_id="t1", surface_id=None)

        plan_ready_calls = [
            call
            for call in executor._emit_surface_update.call_args_list
            if (call.kwargs or {}).get("phase") == "plan_ready"
        ]
        assert len(plan_ready_calls) == 1
        assert plan_ready_calls[0].kwargs["surface_id"] == "run_01"

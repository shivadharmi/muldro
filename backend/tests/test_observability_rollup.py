"""Tests for Phase 3 observability — trace_id propagation and rollup cache."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_execute_run_stamps_trace_id_before_first_step():
    """execute_run must write run.trace_id before any step fires.

    Prior behaviour: trace_id was only assigned when the caller passed one
    in, so perception-triggered background runs and chat-spawned runs left
    run.trace_id null and the detail endpoint fell back to zeros.
    """
    from src.services.graph_executor import GraphExecutor

    # Build a minimal executor with mocked dependencies. The real class has
    # a long init signature; we patch only what execute_run touches.
    executor = GraphExecutor.__new__(GraphExecutor)
    executor._db = AsyncMock()
    executor._active_traces = {}
    executor._cancel_events = {}
    executor._trace_store = None
    executor._audit = AsyncMock()
    executor._emit_event = AsyncMock()
    executor._emit_surface_update = AsyncMock()
    executor._execute_dag = AsyncMock()
    executor._finalize_trace = AsyncMock()
    executor._get_all_steps = AsyncMock(return_value=[])

    run = MagicMock()
    run.run_id = "run_xyz"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.plan_id = "plan_x"
    run.source = "plan"
    run.checkpoint = None
    run.status = "pending"
    run.timeout_seconds = None
    run.error = None
    run.trace_id = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run
    executor._db.execute = AsyncMock(return_value=mock_result)
    executor._db.commit = AsyncMock()

    await executor.execute_run("run_xyz")

    # The trace_id must be non-None after execute_run enters the active
    # path — it's the precondition for the detail endpoint resolving
    # tokens/cost from the Trace table.
    assert run.trace_id is not None
    # And execute_run should default the surface_id to the run_id itself
    # (the canonical run surface id — not a re-prefixed ``run_run_…``).
    assert run.checkpoint["surface_id"] == "run_xyz"


def test_history_trace_info_schema_includes_step_breakdown():
    """HistoryTraceInfo should carry a step_breakdown list so the Trace
    tab can show per-agent/per-step token and cost breakdown.
    """
    from src.api.schemas_history import HistoryTraceInfo, HistoryTraceStep

    info = HistoryTraceInfo(
        trace_id="t1",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.0234,
        duration_ms=1500,
        step_breakdown=[
            HistoryTraceStep(
                step_id="planner", agent="planner", calls=1, input_tokens=500, output_tokens=200
            ),
            HistoryTraceStep(
                step_id="executor", agent="executor", calls=2, input_tokens=500, output_tokens=300
            ),
        ],
    )

    assert len(info.step_breakdown) == 2
    assert info.step_breakdown[0].agent == "planner"
    assert info.step_breakdown[1].agent == "executor"


def test_task_run_model_has_rollup_columns():
    """TaskRun should expose input_tokens, output_tokens, cost_usd as
    first-class columns so history list queries don't need a JOIN.
    """
    from src.models.task_graph import TaskRun

    columns = {c.name for c in TaskRun.__table__.columns}
    assert "input_tokens" in columns
    assert "output_tokens" in columns
    assert "cost_usd" in columns


def test_trace_model_has_run_id_index():
    """Trace.run_id lets the detail endpoint fall back when
    TaskRun.trace_id is null (legacy data path).
    """
    from src.models.traces import Trace

    columns = {c.name for c in Trace.__table__.columns}
    assert "run_id" in columns

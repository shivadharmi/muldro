"""Tests for the pause-time trace checkpoint rollup (D3 / Phase 3).

A run that pauses at the approval gate never reaches ``_finalize_trace`` (that
only fires on a terminal status), so steps executed BEFORE the pause used to
leave ``run.input_tokens=0`` and no Trace row persisted — the trace tab then
rendered a grid of zeros. ``_checkpoint_trace`` rolls the current segment's
partial trace onto the run row and persists it at the pause.

ROLLUP INVARIANT under test: ``run.{input_tokens,output_tokens,cost_usd}`` equal
the SUM over every DISTINCT segment trace_id. Re-rolling the same trace_id is
idempotent (no double-count); a fresh segment trace accumulates on top.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.tracing import JarvisTrace
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _make_executor(settings, mock_db):
    from src.services.graph_executor import GraphExecutor

    return GraphExecutor(settings, mock_db)


def _trace_with_usage(trace_id, *, input_t, output_t, cost):
    """Build a finished-style trace whose totals equal the given numbers."""
    trace = JarvisTrace(trace_id=trace_id, trigger="execution:test")
    span = trace.start_span("executor")
    trace.end_span(
        span.span_id,
        input_tokens=input_t,
        output_tokens=output_t,
        cost_usd=cost,
    )
    return trace


def _make_run(run_id="run_cp1"):
    run = MagicMock()
    run.run_id = run_id
    run.user_id = TEST_USER_ID
    run.workspace_id = "ws_test"
    run.checkpoint = {}
    run.input_tokens = 0
    run.output_tokens = 0
    run.cost_usd = 0.0
    return run


@pytest.mark.asyncio
async def test_checkpoint_rolls_partial_trace_onto_run_and_persists(settings, mock_db):
    """Pausing rolls the live segment's tokens onto the run + stores a Trace."""
    executor = _make_executor(settings, mock_db)
    trace_store = AsyncMock()
    executor._trace_store = trace_store

    run = _make_run()
    trace = _trace_with_usage("trace_seg1", input_t=300, output_t=120, cost=0.0042)
    executor._active_traces[run.run_id] = trace

    await executor._checkpoint_trace(run)

    # Run columns reflect the work done so far.
    assert run.input_tokens == 300
    assert run.output_tokens == 120
    assert run.cost_usd == round(0.0042, 6)
    # A Trace row was persisted, linked to the run.
    trace_store.store_trace.assert_awaited_once()
    _, kwargs = trace_store.store_trace.call_args
    assert kwargs["run_id"] == run.run_id
    # The segment is still LIVE — checkpoint must not pop _active_traces.
    assert executor._active_traces[run.run_id] is trace
    # Rollup ledger is keyed by trace_id for idempotency.
    assert run.checkpoint["trace_rollup"]["trace_seg1"]["input_tokens"] == 300


@pytest.mark.asyncio
async def test_checkpoint_is_idempotent_on_same_trace(settings, mock_db):
    """Checkpointing the same segment twice must not double-count."""
    executor = _make_executor(settings, mock_db)
    executor._trace_store = AsyncMock()

    run = _make_run()
    trace = _trace_with_usage("trace_seg1", input_t=300, output_t=120, cost=0.0042)
    executor._active_traces[run.run_id] = trace

    await executor._checkpoint_trace(run)
    await executor._checkpoint_trace(run)

    assert run.input_tokens == 300
    assert run.output_tokens == 120
    assert run.cost_usd == round(0.0042, 6)


@pytest.mark.asyncio
async def test_finalize_after_checkpoint_does_not_double_count(settings, mock_db):
    """Terminal finalize of a segment already checkpointed must not double-count.

    The pause checkpoint and the terminal finalize fire for the SAME segment
    trace_id, so the run total stays at that single segment's usage.
    """
    executor = _make_executor(settings, mock_db)
    executor._trace_store = AsyncMock()

    run = _make_run()
    trace = _trace_with_usage("trace_seg1", input_t=300, output_t=120, cost=0.0042)
    executor._active_traces[run.run_id] = trace

    await executor._checkpoint_trace(run)
    # finalize pops _active_traces and finishes the same trace
    await executor._finalize_trace(run)

    assert run.input_tokens == 300
    assert run.output_tokens == 120
    assert run.cost_usd == round(0.0042, 6)
    # finalize must pop the live trace.
    assert run.run_id not in executor._active_traces


@pytest.mark.asyncio
async def test_multi_segment_run_accumulates(settings, mock_db):
    """Each resume creates a fresh trace_id; totals sum across segments."""
    executor = _make_executor(settings, mock_db)
    executor._trace_store = AsyncMock()

    run = _make_run()

    # Segment 1 — pause checkpoint.
    seg1 = _trace_with_usage("trace_seg1", input_t=300, output_t=120, cost=0.0042)
    executor._active_traces[run.run_id] = seg1
    await executor._checkpoint_trace(run)

    # Segment 2 — fresh trace (as resume_run would create), then finalize.
    seg2 = _trace_with_usage("trace_seg2", input_t=200, output_t=80, cost=0.0030)
    executor._active_traces[run.run_id] = seg2
    await executor._finalize_trace(run)

    # Run reflects the SUM of both segments.
    assert run.input_tokens == 500
    assert run.output_tokens == 200
    assert run.cost_usd == round(0.0072, 6)
    assert set(run.checkpoint["trace_rollup"].keys()) == {"trace_seg1", "trace_seg2"}


@pytest.mark.asyncio
async def test_checkpoint_noop_without_active_trace(settings, mock_db):
    """No live trace → nothing to roll up, no persist, run columns untouched."""
    executor = _make_executor(settings, mock_db)
    executor._trace_store = AsyncMock()

    run = _make_run()
    await executor._checkpoint_trace(run)

    assert run.input_tokens == 0
    assert run.output_tokens == 0
    executor._trace_store.store_trace.assert_not_awaited()


# ── pause-path wiring: execute_run / resume_run call _checkpoint_trace ──


@pytest.mark.asyncio
async def test_execute_run_checkpoints_when_paused(settings, mock_db):
    """execute_run must checkpoint the trace when the DAG pauses for approval.

    The finally block only finalizes on a terminal status; when the run pauses
    (awaiting_approval) the partial trace would otherwise be lost.
    """
    executor = _make_executor(settings, mock_db)

    run = _make_run("run_paused")
    run.plan_id = "plan_x"
    run.source = "plan"
    run.status = "pending"
    run.timeout_seconds = None
    run.trace_id = None
    run.error = None

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    mock_db.execute = AsyncMock(return_value=run_result)

    executor._audit = AsyncMock()
    executor._emit_event = AsyncMock()
    executor._emit_surface_update = AsyncMock()
    executor._get_all_steps = AsyncMock(return_value=[])
    executor._reconcile_plan_status = AsyncMock()
    executor._finalize_trace = AsyncMock()
    executor._checkpoint_trace = AsyncMock()

    async def _pause(run, **kwargs):
        run.status = "awaiting_approval"

    executor._execute_dag = AsyncMock(side_effect=_pause)

    with patch("src.services.graph_executor.transition_run"):
        await executor.execute_run("run_paused")

    executor._checkpoint_trace.assert_awaited_once_with(run)
    # _finalize_trace still runs in the finally, but it is a no-op once
    # _active_traces was popped — here it's mocked, so just assert both fired.
    executor._finalize_trace.assert_awaited_once_with(run)


@pytest.mark.asyncio
async def test_resume_run_checkpoints_when_paused_again(settings, mock_db):
    """A resumed run that pauses again must checkpoint its new segment."""
    from datetime import datetime, timezone

    executor = _make_executor(settings, mock_db)

    now = datetime.now(timezone.utc)
    run = _make_run("run_resume")
    run.status = "awaiting_approval"
    run.trace_id = "trace_original"
    run.started_at = now
    run.created_at = now
    run.checkpoint = {}
    run.error = None

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    mock_db.execute = AsyncMock(return_value=run_result)

    executor._reconcile_plan_status = AsyncMock()
    executor._finalize_trace = AsyncMock()
    executor._checkpoint_trace = AsyncMock()

    async def _pause_again(run, **kwargs):
        run.status = "awaiting_approval"

    executor._execute_dag = AsyncMock(side_effect=_pause_again)

    with patch("src.services.graph_executor.transition_run"):
        await executor.resume_run("run_resume")

    executor._checkpoint_trace.assert_awaited_once_with(run)

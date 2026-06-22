"""Tests for the run Trace detail tab empty-state (D3 / Phase 3).

When the resolved token/cost totals are all zero, the trace tab must render an
informative alert instead of a metrics grid of zeros (which looks broken). It
distinguishes a run that simply hasn't executed yet from a terminal run that
genuinely made no model calls. The non-zero case keeps the metrics grid.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.surface_detail_builders.run import build_run_trace_tab
from src.ui.contracts import DetailTabResponse


def _mock_run_surface(surface_id: str = "run_abc123"):
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = "run"
    s.payload = {}
    s.workspace_id = "ws_test"
    return s


def _make_run(*, status, input_tokens=0, output_tokens=0, cost_usd=0.0, trace_id=None):
    run = MagicMock()
    run.run_id = "run_abc123"  # post-4893e16: run surface_id IS the run_id
    run.status = status
    run.trace_id = trace_id
    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.cost_usd = cost_usd
    run.started_at = None
    run.completed_at = None
    return run


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


def _scalars_result(values):
    res = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    res.scalars.return_value = scalars
    return res


def _db_for_trace_tab(*, run, trace_row=None, completed_steps=None):
    """Mock db.execute to answer the trace-tab query sequence in order.

    Order: TaskRun lookup → (trace_id Trace lookup) → (reverse run_id Trace
    lookup) → completed-steps scalars (only when totals are zero).
    """
    completed_steps = completed_steps or []
    results = [_scalar_result(run)]
    # build_run_trace_tab does a trace_id lookup only when run.trace_id is set,
    # then always a reverse run_id lookup if the first missed.
    if run.trace_id:
        results.append(_scalar_result(trace_row))
        if trace_row is None:
            results.append(_scalar_result(None))
    else:
        results.append(_scalar_result(trace_row))
    # Zero-total path queries completed steps next.
    results.append(_scalars_result(completed_steps))

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


@pytest.mark.asyncio
async def test_zero_totals_non_terminal_says_not_executed():
    """pending/awaiting run with no completed steps → 'hasn't executed' message."""
    run = _make_run(status="awaiting_approval")
    db = _db_for_trace_tab(run=run, completed_steps=[])

    result = await build_run_trace_tab(db, _mock_run_surface())

    assert isinstance(result, DetailTabResponse)
    assert result.tab_id == "trace"
    rendered = str(result.model_dump())
    assert "hasn't executed" in rendered
    # Must NOT render the zeros metrics grid (no metric labels).
    assert "Input tokens" not in rendered


@pytest.mark.asyncio
async def test_zero_totals_terminal_says_no_usage():
    """completed run with zero model usage → 'No model usage recorded' message."""
    run = _make_run(status="completed")
    db = _db_for_trace_tab(run=run, completed_steps=[])

    result = await build_run_trace_tab(db, _mock_run_surface())

    rendered = str(result.model_dump())
    assert "No model usage recorded" in rendered
    assert "Input tokens" not in rendered


@pytest.mark.asyncio
async def test_zero_totals_non_terminal_but_has_completed_steps_says_no_usage():
    """A still-running run that already completed steps with no usage is treated
    as 'no usage', not 'hasn't executed'."""
    run = _make_run(status="running")
    db = _db_for_trace_tab(run=run, completed_steps=[MagicMock()])

    result = await build_run_trace_tab(db, _mock_run_surface())

    rendered = str(result.model_dump())
    assert "No model usage recorded" in rendered


@pytest.mark.asyncio
async def test_nonzero_totals_render_metrics_grid_regression():
    """Non-zero totals must still render the trace metrics grid (regression)."""
    trace_row = MagicMock()
    trace_row.trace_id = "trace_1"
    trace_row.total_input_tokens = 1000
    trace_row.total_output_tokens = 400
    trace_row.total_cost_usd = 0.0123
    trace_row.duration_ms = 1500

    # Headline totals come from the run rollup; here only the trace_row carries
    # them (legacy single-segment run), so the run.* fallback to trace_row applies.
    run = _make_run(status="completed", trace_id="trace_1")

    # Sequence: run lookup → trace_id hit → breakdown JOIN (all segments) →
    # legacy fallback by trace_id (only because the JOIN returned no calls).
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(run),
            _scalar_result(trace_row),
            _scalars_result([]),  # JOIN across run segments → empty
            _scalars_result([]),  # legacy fallback by trace_id → empty
        ]
    )

    result = await build_run_trace_tab(db, _mock_run_surface())

    rendered = str(result.model_dump())
    # The metrics grid renders the labelled metric tiles.
    assert "Input tokens" in rendered
    assert "Output tokens" in rendered
    assert "hasn't executed" not in rendered
    assert "No model usage recorded" not in rendered


@pytest.mark.asyncio
async def test_multi_segment_run_uses_rollup_not_single_trace():
    """A resumed run's headline must reflect the cross-segment run.* rollup, not the
    first segment's trace_row. run.trace_id points at segment 1 only."""
    trace_row = MagicMock()  # segment 1 trace
    trace_row.trace_id = "trace_seg1"
    trace_row.total_input_tokens = 300
    trace_row.total_output_tokens = 120
    trace_row.total_cost_usd = 0.0042
    trace_row.duration_ms = 800

    # run rollup reflects ALL segments (300+200 in, 120+80 out).
    run = _make_run(
        status="completed",
        input_tokens=500,
        output_tokens=200,
        cost_usd=0.0072,
        trace_id="trace_seg1",
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(run),
            _scalar_result(trace_row),
            _scalars_result([]),  # breakdown JOIN
            _scalars_result([]),  # legacy fallback
        ]
    )

    result = await build_run_trace_tab(db, _mock_run_surface())

    rendered = str(result.model_dump())
    # Must show the rollup total (500 in / 200 out), NOT the single trace (300 / 120).
    assert "500" in rendered
    assert "200" in rendered
    assert "300" not in rendered

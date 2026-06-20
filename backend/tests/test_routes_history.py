"""Tests for history API response schemas and endpoints."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestHistorySchemas:
    def test_history_step_response_shape(self):
        from src.api.schemas_history import HistoryStepSummary

        step = HistoryStepSummary(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
        )
        assert step.step_id == "step_001"
        assert step.status == "completed"

    def test_history_item_response_shape(self):
        from src.api.schemas_history import HistoryItemResponse

        item = HistoryItemResponse(
            run_id="run_001",
            plan_id="plan_001",
            goal="Send investor email",
            source="background",
            trigger_type="event",
            status="completed",
            risk_level=None,
            started_at=datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 18, tzinfo=timezone.utc),
            error=None,
            retry_count=0,
            step_count=3,
            completed_step_count=3,
            cost_usd=0.004,
            steps=[],
            approval=None,
            live_phase=None,
            surface_id=None,
        )
        assert item.run_id == "run_001"
        assert item.step_count == 3

    def test_history_list_response_shape(self):
        from src.api.schemas_history import HistoryListResponse

        resp = HistoryListResponse(items=[], total=0, limit=20, offset=0)
        assert resp.total == 0
        assert resp.limit == 20

    def test_history_detail_step_includes_output(self):
        from src.api.schemas_history import HistoryDetailStep

        step = HistoryDetailStep(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            input_data={"query": "investor"},
            output_data={"result": "Found 3 threads"},
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
            duration_ms=2340,
            error=None,
            artifacts=[],
        )
        assert step.output_data == {"result": "Found 3 threads"}
        assert step.duration_ms == 2340


# ---------------------------------------------------------------------------
# Helpers for mocking SQLAlchemy execute() results
# ---------------------------------------------------------------------------


class _FakeScalars:
    """Mimics the scalars() result from db.execute()."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Mimics the result of db.execute()."""

    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return _FakeScalars(self._rows)

    def scalar_one_or_none(self):
        # Mirror SQLAlchemy semantics: raises when >1 row is present.
        if len(self._rows) > 1:
            from sqlalchemy.exc import MultipleResultsFound

            raise MultipleResultsFound("Multiple rows were found when one or none was required")
        if self._rows:
            return self._rows[0]
        return self._scalar

    def scalar(self):
        return self._scalar


# ---------------------------------------------------------------------------
# list_history tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_history_returns_items_with_correct_shape():
    """list_history() should return a HistoryListResponse with correct fields."""
    from src.api.routes_history import list_history

    # Build mock TaskRun
    run = MagicMock()
    run.run_id = "run_abc"
    run.plan_id = "plan_abc"
    run.user_id = "usr_01JTEST00000000000000000000"
    run.workspace_id = "ws_test"
    run.status = "completed"
    run.source = "plan"
    run.retry_count = 0
    run.started_at = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)
    run.completed_at = datetime(2026, 4, 13, 10, 0, 30, tzinfo=timezone.utc)
    run.error = None

    # Build mock TaskStep
    step = MagicMock()
    step.step_id = "step_001"
    step.name = "Search emails"
    step.input_data = {"capability": "email.search"}
    step.status = "completed"
    step.started_at = datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc)
    step.completed_at = datetime(2026, 4, 13, 10, 0, 5, tzinfo=timezone.utc)

    # Build mock Plan
    plan = MagicMock()
    plan.goal = "Send investor email"
    plan.trigger_type = "event"
    plan.risk_level = "low"

    # db.execute() is called multiple times in sequence:
    #   1) count query → scalar() returns total
    #   2) runs query → scalars().all() returns [run]
    #   3) steps query → scalars().all() returns [step]
    #   4) plan query → scalar_one_or_none() returns plan
    #   5) approval query → scalar_one_or_none() returns None (no approval)
    # UISurface lookup is wrapped in try/except — provide a result that yields None
    execute_results = [
        _FakeResult(scalar=1),  # count
        _FakeResult(rows=[run]),  # runs
        _FakeResult(rows=[step]),  # steps
        _FakeResult(scalar=plan),  # plan
        _FakeResult(scalar=None),  # approval
    ]
    call_index = 0

    async def fake_execute(_stmt, *args, **kwargs):
        nonlocal call_index
        result = execute_results[call_index] if call_index < len(execute_results) else _FakeResult()
        call_index += 1
        return result

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    with patch("src.models.ui_state.UISurface", create=True):
        resp = await list_history(
            status="all",
            source="all",
            search=None,
            date_from=None,
            date_to=None,
            limit=20,
            offset=0,
            user_id="usr_01JTEST00000000000000000000",
            workspace_id="ws_test",
            db=mock_db,
        )

    assert resp.total == 1
    assert resp.limit == 20
    assert resp.offset == 0
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.run_id == "run_abc"
    assert item.goal == "Send investor email"
    assert item.step_count == 1
    assert item.status == "completed"
    assert item.approval is None


@pytest.mark.asyncio
async def test_list_history_handles_multiple_pending_approvals():
    """A run with >1 pending approvals must not 500 (regression for MultipleResultsFound).

    The approval query must be 0/1/many-safe and deterministically pick the most
    recent pending approval rather than raising sqlalchemy.exc.MultipleResultsFound.
    """
    from src.api.routes_history import list_history

    run = MagicMock()
    run.run_id = "run_multi"
    run.plan_id = None
    run.user_id = "usr_01JTEST00000000000000000000"
    run.workspace_id = "ws_test"
    run.status = "awaiting_approval"
    run.source = "background"
    run.retry_count = 0
    run.started_at = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)
    run.completed_at = None
    run.error = None

    # Two pending approvals for the SAME run, newest first (ordered by created_at desc).
    appr_newer = MagicMock()
    appr_newer.approval_id = "apr_newer"
    appr_newer.step_id = "step_002"
    appr_newer.title = "Send follow-up email"
    appr_newer.risk_level = "medium"
    appr_newer.created_at = datetime(2026, 4, 13, 10, 0, 5, tzinfo=timezone.utc)

    appr_older = MagicMock()
    appr_older.approval_id = "apr_older"
    appr_older.step_id = "step_001"
    appr_older.title = "Create calendar event"
    appr_older.risk_level = "low"
    appr_older.created_at = datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc)

    # db.execute() call sequence for one awaiting_approval run (no plan_id):
    #   1) count, 2) runs, 3) steps, 4) approval (2 pending rows)
    execute_results = [
        _FakeResult(scalar=1),  # count
        _FakeResult(rows=[run]),  # runs
        _FakeResult(rows=[]),  # steps (none)
        _FakeResult(rows=[appr_newer, appr_older]),  # approval — MULTIPLE pending
    ]
    call_index = 0

    async def fake_execute(_stmt, *args, **kwargs):
        nonlocal call_index
        result = execute_results[call_index] if call_index < len(execute_results) else _FakeResult()
        call_index += 1
        return result

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    with patch("src.models.ui_state.UISurface", create=True):
        resp = await list_history(
            status="all",
            source="all",
            search=None,
            date_from=None,
            date_to=None,
            limit=20,
            offset=0,
            user_id="usr_01JTEST00000000000000000000",
            workspace_id="ws_test",
            db=mock_db,
        )

    # No exception raised; the run surfaces with exactly one approval (the newest).
    assert resp.total == 1
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.approval is not None
    assert item.approval.approval_id == "apr_newer"
    assert item.approval.step_id == "step_002"


# ---------------------------------------------------------------------------
# GET /v1/history/{run_id} — detail endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_detail_returns_full_context():
    """get_history_detail() returns full run context including plan and step output."""
    from src.api.routes_history import get_history_detail

    # Build mock TaskRun
    run = MagicMock()
    run.run_id = "run_detail"
    run.plan_id = "plan_detail"
    run.user_id = "usr_01JTEST00000000000000000000"
    run.workspace_id = "ws_test"
    run.status = "completed"
    run.source = "plan"
    run.retry_count = 0
    run.trace_id = "trace_001"
    run.started_at = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)
    run.completed_at = datetime(2026, 4, 13, 10, 0, 30, tzinfo=timezone.utc)
    run.error = None

    # Build mock TaskStep with input/output data
    step = MagicMock()
    step.step_id = "step_detail_001"
    step.name = "Search emails"
    step.input_data = {"capability": "email.search", "query": "investor"}
    step.output_data = {"result": "Found 3 threads"}
    step.status = "completed"
    step.error = None
    step.started_at = datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc)
    step.completed_at = datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc)

    # Build mock Plan
    plan = MagicMock()
    plan.plan_id = "plan_detail"
    plan.goal = "Find investor emails"
    plan.reasoning_summary = "User requested email search"
    plan.success_conditions = ["emails found"]
    plan.trigger_type = "user_message"
    plan.priority = "medium"

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # TaskRun
            return _FakeResult(scalar=run)
        elif call_count == 2:  # TaskSteps
            return _FakeResult(rows=[step])
        elif call_count == 3:  # Artifact query (per-step, inside try/except)
            return _FakeResult(rows=[])
        elif call_count == 4:  # Plan
            return _FakeResult(scalar=plan)
        elif call_count == 5:  # Approvals
            return _FakeResult(rows=[])
        elif call_count == 6:  # RuntimeEvents
            return _FakeResult(rows=[])
        return _FakeResult()

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    resp = await get_history_detail(
        run_id="run_detail",
        user_id="usr_01JTEST00000000000000000000",
        workspace_id="ws_test",
        db=mock_db,
    )

    assert resp.run_id == "run_detail"
    assert resp.plan is not None
    assert resp.plan.goal == "Find investor emails"
    assert len(resp.steps) == 1
    assert resp.steps[0].output_data == {"result": "Found 3 threads"}
    assert resp.steps[0].step_id == "step_detail_001"


@pytest.mark.asyncio
async def test_history_detail_returns_404_for_missing_run():
    """get_history_detail() raises 404 HTTPException when run not found."""
    from fastapi import HTTPException

    from src.api.routes_history import get_history_detail

    async def fake_execute(stmt):
        return _FakeResult(scalar=None)

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    with pytest.raises(HTTPException) as exc_info:
        await get_history_detail(
            run_id="run_nonexistent",
            user_id="usr_01JTEST00000000000000000000",
            workspace_id="ws_test",
            db=mock_db,
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Helper to build a minimal TaskRun mock
# ---------------------------------------------------------------------------


def _make_task_run(
    run_id: str = "run_001",
    status: str = "failed",
    user_id: str = "usr_01JTEST00000000000000000000",
    workspace_id: str = "ws_test",
) -> MagicMock:
    run = MagicMock()
    run.run_id = run_id
    run.plan_id = "plan_001"
    run.user_id = user_id
    run.workspace_id = workspace_id
    run.status = status
    run.source = "background"
    run.retry_count = 0
    run.error = {"msg": "boom"}
    run.completed_at = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)
    run.started_at = datetime(2026, 4, 13, 9, 59, tzinfo=timezone.utc)
    return run


# ---------------------------------------------------------------------------
# POST /v1/history/{run_id}/retry tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_transitions_failed_run_to_pending():
    """retry_run() calls transition_run with 'pending' for a failed run."""
    from fastapi import HTTPException  # noqa: F401

    from src.api.routes_history import retry_run

    run = _make_task_run(status="failed")

    async def fake_execute(_stmt, *args, **kwargs):
        return _FakeResult(scalar=run)

    mock_db = MagicMock()
    mock_db.execute = fake_execute
    mock_db.commit = MagicMock(return_value=None)

    async def async_commit():
        return None

    mock_db.commit = async_commit

    with patch("src.api.routes_history.transition_run") as mock_transition:
        resp = await retry_run(
            run_id="run_001",
            user_id="usr_01JTEST00000000000000000000",
            workspace_id="ws_test",
            db=mock_db,
        )

    mock_transition.assert_called_once_with(run, "pending")
    assert resp.run_id == "run_001"
    assert resp.status == run.status  # status reflects whatever transition_run set


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_run():
    """retry_run() raises 400 HTTPException for a completed run."""
    from fastapi import HTTPException

    from src.api.routes_history import retry_run

    run = _make_task_run(status="completed")

    async def fake_execute(_stmt, *args, **kwargs):
        return _FakeResult(scalar=run)

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            run_id="run_001",
            user_id="usr_01JTEST00000000000000000000",
            workspace_id="ws_test",
            db=mock_db,
        )

    assert exc_info.value.status_code == 400

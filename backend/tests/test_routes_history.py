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

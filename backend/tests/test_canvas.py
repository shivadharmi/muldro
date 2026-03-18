"""Tests for Canvas UI endpoints — dashboard, approval detail, task detail."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id, get_session
from src.config.settings import get_settings
from tests.conftest import TEST_USER_ID


def _make_mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.scalar = AsyncMock(return_value=0)
    db.commit = AsyncMock()
    return db


def _make_briefing():
    b = MagicMock()
    b.briefing_id = "brief_001"
    b.user_id = TEST_USER_ID
    b.briefing_date = date.today()
    b.headline = "3 priorities, 2 approvals pending"
    b.recommended_actions = ["Reply to investor email", "Review PR"]
    return b


def _make_approval(approval_id="apr_001", status="pending"):
    a = MagicMock()
    a.approval_id = approval_id
    a.user_id = TEST_USER_ID
    a.title = "Send reply to investor"
    a.summary = "Draft email to John Doe about Series B"
    a.approval_type = "send_email"
    a.risk_level = "medium"
    a.status = status
    a.execution_id = "exec_001"
    a.created_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)
    a.decided_at = None
    a.decision_reason = None
    a.artifact_refs = None
    return a


def _make_plan(plan_id="plan_001"):
    p = MagicMock()
    p.plan_id = plan_id
    p.user_id = TEST_USER_ID
    p.goal = "Draft reply to investor email"
    p.priority = "high"
    p.status = "executing"
    p.decision = "draft_email"
    p.risk_level = "medium"
    p.reasoning_summary = "Investor follow-up is time-sensitive"
    p.created_at = datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc)
    return p


def _make_plan_task(task_id="ptask_001", task_type="draft_email", status="completed"):
    pt = MagicMock()
    pt.task_id = task_id
    pt.plan_id = "plan_001"
    pt.task_type = task_type
    pt.status = status
    return pt


def _make_task_run(run_id="run_001", status="completed"):
    r = MagicMock()
    r.run_id = run_id
    r.plan_id = "plan_001"
    r.user_id = TEST_USER_ID
    r.status = status
    r.source = "plan"
    r.execution_mode = "auto_execute"
    r.created_at = datetime(2026, 3, 13, 9, 30, tzinfo=timezone.utc)
    return r


def _make_task_step(task_id="ptask_001", status="completed"):
    s = MagicMock()
    s.step_id = "step_001"
    s.run_id = "run_001"
    s.task_id = task_id
    s.plan_task_id = task_id
    s.status = status
    s.output_data = {"summary": "Email drafted successfully"}
    s.error = None
    return s


def _make_meeting_event():
    m = MagicMock()
    m.event_id = "evt_meeting_001"
    m.title = "Board sync"
    m.occurred_at = datetime.now(timezone.utc) + timedelta(hours=2)
    m.actor_entities = [{"email": "alice@co.com"}, {"email": "bob@co.com"}]
    return m


@pytest.fixture(autouse=True)
def _override_deps():
    """Override FastAPI deps to avoid real DB/auth for all tests in this module."""
    mock_settings = MagicMock()
    mock_settings.backend_token = ""

    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_settings] = lambda: mock_settings
    yield
    app.dependency_overrides.clear()


# ── Dashboard tests ──────────────────────────────────────────────


def test_dashboard_returns_all_sections():
    """Dashboard should return approvals, tasks, meetings, and briefing headline."""
    db = _make_mock_db()
    briefing = _make_briefing()
    approval = _make_approval()
    plan = _make_plan()
    meeting = _make_meeting_event()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:  # briefing
            result.scalar_one_or_none.return_value = briefing
        elif call_count == 2:  # approvals
            result.scalars.return_value.all.return_value = [approval]
        elif call_count == 3:  # plans
            result.scalars.return_value.all.return_value = [plan]
        elif call_count == 5:  # meetings
            result.scalars.return_value.all.return_value = [meeting]
        else:
            result.scalars.return_value.all.return_value = []
        return result

    db.execute = mock_execute
    db.scalar = AsyncMock(return_value=2)
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/canvas/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["headline"] == "3 priorities, 2 approvals pending"
    assert len(data["pending_approvals"]) == 1
    assert data["pending_approvals"][0]["approval_id"] == "apr_001"
    assert len(data["active_tasks"]) == 1
    assert data["active_tasks"][0]["goal"] == "Draft reply to investor email"
    assert data["active_tasks"][0]["step_count"] == 2
    assert len(data["recommended_actions"]) == 2


def test_dashboard_empty_state():
    """Dashboard should handle empty state gracefully."""
    db = _make_mock_db()

    async def mock_execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = mock_execute
    db.scalar = AsyncMock(return_value=0)
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/canvas/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["headline"] is None
    assert data["pending_approvals"] == []
    assert data["active_tasks"] == []
    assert data["upcoming_meetings"] == []


# ── Approval detail tests ────────────────────────────────────────


def test_approval_detail():
    """Should return detailed approval info with plan context."""
    db = _make_mock_db()
    approval = _make_approval()
    run = _make_task_run()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:  # approval lookup
            result.scalar_one_or_none.return_value = approval
        elif call_count == 2:  # TaskRun lookup
            result.scalar_one_or_none.return_value = run
        elif call_count == 3:  # plan goal
            result.scalar_one_or_none.return_value = "Draft reply to investor email"
        return result

    db.execute = mock_execute
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/approvals/apr_001")

    assert resp.status_code == 200
    data = resp.json()
    assert data["approval_id"] == "apr_001"
    assert data["approval_type"] == "send_email"
    assert data["plan_goal"] == "Draft reply to investor email"
    assert data["execution_id"] == "exec_001"


def test_approval_detail_not_found():
    """Should return 404 for unknown approval."""
    db = _make_mock_db()

    async def mock_execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = mock_execute
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/approvals/apr_nonexistent")
    assert resp.status_code == 404


# ── Task detail tests ────────────────────────────────────────────


def test_task_detail_with_steps():
    """Should return task detail with execution steps and progress."""
    db = _make_mock_db()
    plan = _make_plan()
    plan_task = _make_plan_task()
    run = _make_task_run()
    step = _make_task_step()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:  # plan lookup
            result.scalar_one_or_none.return_value = plan
        elif call_count == 2:  # plan tasks
            result.scalars.return_value.all.return_value = [plan_task]
        elif call_count == 3:  # TaskRun by plan_id
            result.scalar_one_or_none.return_value = run
        elif call_count == 4:  # TaskStep by run_id
            result.scalars.return_value.all.return_value = [step]
        return result

    db.execute = mock_execute
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/tasks/plan_001")

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "plan_001"
    assert data["goal"] == "Draft reply to investor email"
    assert data["execution_status"] == "completed"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["status"] == "completed"
    assert data["steps"][0]["result_summary"] == "Email drafted successfully"


def test_task_detail_not_found():
    """Should return 404 for unknown task."""
    db = _make_mock_db()

    async def mock_execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = mock_execute
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/v1/tasks/plan_nonexistent")
    assert resp.status_code == 404

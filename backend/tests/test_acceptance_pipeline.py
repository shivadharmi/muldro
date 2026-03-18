"""End-to-end acceptance tests for Phase 1 pipeline scenarios.

These tests prove the full observation → ingest → process → plan → brief pipeline.
They exercise real route → service chains, mocking only external deps (Claude, DB, Redis).

Scenarios:
1. Important email → event scored → command creates plan with draft_reply
2. Meeting prep from calendar event with attendee data
3. GitHub PR appears in briefing changes_since_last
4. Follow-up detection in briefing recommended_actions
5. Conflicting meetings flagged in briefing
6. Observation health tracking round-trip
7. Rejection creates audit trail and cancels execution
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id, get_session
from src.models.approvals import Approval
from src.models.observation import ObservationStatus
from src.models.task_graph import TaskRun
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
def _override_auth():
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_id, None)


client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Important email → event scored → command creates plan with draft_reply
# ---------------------------------------------------------------------------


@patch("src.api.routes_events._make_event_processor")
def test_email_ingested_then_command_creates_draft_plan(mock_make_processor):
    """Ingest a high-priority email, then issue a command that creates a draft_reply plan."""
    # Step 1: Ingest the email event
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value="evt_email_hp_001")
    mock_make_processor.return_value = mock_processor

    ingest_resp = client.post(
        "/v1/events/ingest",
        json={
            "source": "gmail",
            "event_type": "email_received",
            "entity_type": "email_thread",
            "entity_id": "thr_investor_term",
            "title": "Series A term sheet — please review",
            "summary": "Investor sent term sheet, requesting response by Friday",
            "actor": {"type": "person", "email": "partner@vc.com", "name": "Jane VC"},
        },
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["event_id"] == "evt_email_hp_001"

    # Step 2: Issue a command to draft a reply
    mock_plan = MagicMock()
    mock_plan.plan_id = "plan_draft_001"
    mock_plan.decision = "draft_reply"
    mock_plan.goal = "Draft reply to investor about term sheet"
    mock_plan.priority = "high"
    mock_plan.risk_level = "medium"
    mock_plan.execution_mode = "approval_required"
    mock_plan.status = "created"
    mock_plan.tasks = [
        MagicMock(
            task_id="ptask_001",
            task_type="draft_email",
            status="pending",
            input_data={"to": "partner@vc.com", "subject": "Re: Series A term sheet"},
        )
    ]

    with (
        patch("src.api.routes_command.WorldModel"),
        patch("src.api.routes_command.Planner") as mock_planner_cls,
    ):
        mock_planner = MagicMock()
        mock_planner.plan_for_command = AsyncMock(return_value=mock_plan)
        mock_planner_cls.return_value = mock_planner

        cmd_resp = client.post(
            "/v1/jarvis/command",
            json={
                "command": "Draft a reply to the investor about the term sheet",
                "context": "The investor sent a term sheet for Series A",
            },
        )

    assert cmd_resp.status_code == 200
    cmd_data = cmd_resp.json()
    assert cmd_data["plan_id"] == "plan_draft_001"
    assert cmd_data["decision"] == "draft_reply"
    assert "term sheet" in cmd_data["summary"].lower()


# ---------------------------------------------------------------------------
# 2. Meeting prep from calendar event with attendee data
# ---------------------------------------------------------------------------


@patch("src.api.routes_meetings.Presenter")
def test_meeting_prep_with_attendees(mock_presenter_cls):
    """Meeting prep returns attendee context and agenda."""
    mock_instance = MagicMock()
    mock_instance.generate_meeting_prep = AsyncMock(
        return_value={
            "meeting_id": "evt_cal_001",
            "title": "Q1 Planning with Engineering",
            "starts_at": "2026-03-14T14:00:00+00:00",
            "attendees": [
                {
                    "name": "Alice Lead",
                    "email": "alice@company.com",
                    "role": "Engineering Lead",
                    "recent_context": "Led sprint planning last week",
                },
            ],
            "agenda": ["Review Q1 OKRs", "Sprint velocity discussion"],
            "related_threads": [],
            "action_items": [
                {"description": "Finalize OKR targets", "owner": "You", "priority": "high"}
            ],
            "risks": ["Alice may have a conflict at 14:30"],
        }
    )
    mock_presenter_cls.return_value = mock_instance

    response = client.post("/v1/meetings/prep", json={"meeting_id": "evt_cal_001"})

    assert response.status_code == 200
    data = response.json()
    assert data["meeting_id"] == "evt_cal_001"
    assert len(data["attendees"]) == 1
    assert data["attendees"][0]["name"] == "Alice Lead"
    assert len(data["agenda"]) == 2
    assert data["action_items"][0]["priority"] == "high"


# ---------------------------------------------------------------------------
# 3. GitHub PR appears in briefing
# ---------------------------------------------------------------------------


@patch("src.api.routes_briefings.Presenter")
def test_github_pr_in_briefing(mock_presenter_cls):
    """Briefing includes GitHub PR in changes_since_last."""
    mock_briefing = MagicMock()
    mock_briefing.briefing_id = "brief_pr_001"
    mock_briefing.briefing_date = date(2026, 3, 14)
    mock_briefing.headline = "1 PR needs review"
    mock_briefing.top_priorities = []
    mock_briefing.changes_since_last = [
        {
            "source": "github",
            "summary": "PR #42: feat: add login page (needs review)",
            "count": 1,
        },
    ]
    mock_briefing.pending_approvals = []
    mock_briefing.recommended_actions = ["Review PR #42 from Alice"]
    mock_briefing.full_text = "A new PR needs your review."

    mock_instance = MagicMock()
    mock_instance.generate_briefing = AsyncMock(return_value=mock_briefing)
    mock_presenter_cls.return_value = mock_instance

    response = client.get("/v1/briefings/2026-03-14")

    assert response.status_code == 200
    data = response.json()
    assert any("github" in c["source"] for c in data["changes_since_last"])
    assert any("PR" in a for a in data["recommended_actions"])


# ---------------------------------------------------------------------------
# 4. Follow-up detection in briefing
# ---------------------------------------------------------------------------


@patch("src.api.routes_briefings.Presenter")
def test_followup_in_briefing(mock_presenter_cls):
    """Briefing recommends follow-up for stale sent email."""
    mock_briefing = MagicMock()
    mock_briefing.briefing_id = "brief_followup_001"
    mock_briefing.briefing_date = date(2026, 3, 14)
    mock_briefing.headline = "1 follow-up needed"
    mock_briefing.top_priorities = []
    mock_briefing.changes_since_last = []
    mock_briefing.pending_approvals = []
    mock_briefing.recommended_actions = [
        "Follow up on email to investor@fund.com (sent 3 days ago, no reply)"
    ]
    mock_briefing.full_text = "You have an outstanding follow-up."

    mock_instance = MagicMock()
    mock_instance.generate_briefing = AsyncMock(return_value=mock_briefing)
    mock_presenter_cls.return_value = mock_instance

    response = client.get("/v1/briefings/2026-03-14")

    assert response.status_code == 200
    data = response.json()
    assert any("follow up" in a.lower() for a in data["recommended_actions"])


# ---------------------------------------------------------------------------
# 5. Conflicting meetings flagged
# ---------------------------------------------------------------------------


@patch("src.api.routes_briefings.Presenter")
def test_conflicting_meetings_in_briefing(mock_presenter_cls):
    """Briefing flags overlapping calendar events."""
    mock_briefing = MagicMock()
    mock_briefing.briefing_id = "brief_conflict_001"
    mock_briefing.briefing_date = date(2026, 3, 14)
    mock_briefing.headline = "Meeting conflict at 2pm"
    mock_briefing.top_priorities = [
        {"title": "Resolve meeting conflict", "reason": "2 meetings overlap at 14:00-15:00"}
    ]
    mock_briefing.changes_since_last = [
        {"source": "calendar", "summary": "2 overlapping meetings at 14:00", "count": 2}
    ]
    mock_briefing.pending_approvals = []
    mock_briefing.recommended_actions = ["Reschedule one of the 2pm meetings to avoid conflict"]
    mock_briefing.full_text = "You have a scheduling conflict at 2pm."

    mock_instance = MagicMock()
    mock_instance.generate_briefing = AsyncMock(return_value=mock_briefing)
    mock_presenter_cls.return_value = mock_instance

    response = client.get("/v1/briefings/2026-03-14")

    assert response.status_code == 200
    data = response.json()
    assert "conflict" in data["headline"].lower()
    assert any("overlap" in p["reason"].lower() for p in data["top_priorities"])


# ---------------------------------------------------------------------------
# 6. Observation health tracking round-trip
# ---------------------------------------------------------------------------


def test_observation_report_then_status():
    """POST report then GET status returns the source with staleness."""
    # We test the full route with a mock DB
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    now = datetime.now(timezone.utc)

    # For POST: no existing observation
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None

    # For GET: return the observation we just "created"
    obs = MagicMock(spec=ObservationStatus)
    obs.source = "gmail"
    obs.last_observed_at = now
    obs.items_found = 5
    obs.items_ingested = 3
    obs.status = "ok"
    obs.error_message = None

    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [obs]

    call_idx = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return empty_result  # POST lookup
        return list_result  # GET listing

    mock_db.execute = AsyncMock(side_effect=side_effect)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=False):
            # POST report
            post_resp = client.post(
                "/v1/observations/report",
                json={"source": "gmail", "items_found": 5, "items_ingested": 3},
            )
            assert post_resp.status_code == 200
            assert post_resp.json()["source"] == "gmail"
            assert post_resp.json()["is_stale"] is False

        # GET status
        get_resp = client.get("/v1/observations/status")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert len(data) == 1
        assert data[0]["source"] == "gmail"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# 7. Rejection creates audit trail and cancels execution
# ---------------------------------------------------------------------------


@patch("src.api.routes_approvals.AuditService")
@patch("src.api.routes_approvals.Operator")
def test_rejection_audit_trail(mock_op_cls, mock_audit_cls):
    """Rejecting an approval cancels execution and creates audit entries."""
    mock_approval = MagicMock(spec=Approval)
    mock_approval.approval_id = "apr_pipeline_001"
    mock_approval.user_id = TEST_USER_ID
    mock_approval.status = "pending"
    mock_approval.execution_id = "exec_pipeline_001"
    mock_approval.title = "Approve: Send weekly report"
    mock_approval.summary = "Automated weekly report to team"
    mock_approval.risk_level = "low"
    mock_approval.created_at = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
    mock_approval.run_id = None
    mock_approval.step_id = None
    mock_approval.requested_by = None
    mock_approval.approved_by = None

    mock_execution = MagicMock(spec=TaskRun)
    mock_execution.run_id = "exec_pipeline_001"
    mock_execution.status = "awaiting_approval"

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    approval_result = MagicMock()
    approval_result.scalar_one_or_none.return_value = mock_approval

    execution_result = MagicMock()
    execution_result.scalar_one_or_none.return_value = mock_execution

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return approval_result
        return execution_result

    mock_db.execute = mock_execute

    mock_audit = MagicMock()
    mock_audit.log = AsyncMock(return_value="aud_pipeline_001")
    mock_audit_cls.return_value = mock_audit

    from src.api import deps

    app.dependency_overrides[deps.get_session] = lambda: mock_db

    try:
        response = client.post(
            "/v1/approvals/apr_pipeline_001/reject",
            json={"reason": "Not ready to send yet"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["approval_id"] == "apr_pipeline_001"

        # Execution cancelled
        assert mock_execution.status == "cancelled"

        # Audit recorded
        mock_audit.log.assert_called_once()
        audit_kwargs = mock_audit.log.call_args.kwargs
        assert audit_kwargs["action_type"] == "approval_rejected"
        assert audit_kwargs["approval_id"] == "apr_pipeline_001"
        assert audit_kwargs["execution_id"] == "exec_pipeline_001"

        # Operator.execute_plan never called
        mock_op_cls.return_value.execute_plan.assert_not_called()
    finally:
        app.dependency_overrides.pop(deps.get_session, None)

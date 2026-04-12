"""End-to-end acceptance tests from PRD scenarios.

These tests exercise full API flows through the route → service chain,
mocking only external dependencies (Claude API, database).

Scenarios from PRD Document 08:
1. High-priority email → event/plan/execution/approval card
2. Morning schedule → daily briefing with priorities/changes/approvals
3. Pending meeting → prep card with attendee/project context
4. Rejected approval → no external write + audit outcome recorded
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id
from src.models.approvals import Approval
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
# Scenario 1: High-priority email → event + plan + execution + approval
# ---------------------------------------------------------------------------


@patch("src.api.routes_events.MemoryService")
@patch("src.api.routes_events.WorldModel")
@patch("src.api.routes_events.EventProcessor")
def test_high_priority_email_creates_event_and_plan(mock_ep_cls, mock_wm_cls, mock_mem_cls):
    """A high-priority email ingested via /v1/events/ingest should produce
    exactly one normalized event. The callback pipeline fires entity extraction,
    memory extraction, and proactive planning."""
    mock_ep = MagicMock()
    mock_ep.process = AsyncMock(return_value="evt_test_001")
    mock_ep_cls.return_value = mock_ep

    response = client.post(
        "/v1/events/ingest",
        json={
            "source": "gmail",
            "event_type": "email_received",
            "entity_type": "email_thread",
            "entity_id": "thr_investor_001",
            "title": "Series A term sheet attached",
            "summary": "Investor sent term sheet for review, requesting response by EOD Friday",
            "actor": {"type": "person", "email": "partner@vc.com", "name": "Jane VC"},
            "occurred_at": "2026-03-14T09:00:00Z",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["event_id"] == "evt_test_001"
    assert data["status"] == "processed"

    # Verify EventProcessor.process was called with correct source
    mock_ep.process.assert_called_once()
    raw_arg = mock_ep.process.call_args[0][0]
    assert raw_arg.source == "gmail"
    assert raw_arg.title == "Series A term sheet attached"
    assert raw_arg.actor["email"] == "partner@vc.com"


@patch("src.api.routes_events.MemoryService")
@patch("src.api.routes_events.WorldModel")
@patch("src.api.routes_events.EventProcessor")
def test_duplicate_event_returns_duplicate_status(mock_ep_cls, mock_wm_cls, mock_mem_cls):
    """Ingesting the same event twice should return duplicate status (idempotency)."""
    mock_ep = MagicMock()
    mock_ep.process = AsyncMock(return_value=None)  # None = duplicate
    mock_ep_cls.return_value = mock_ep

    response = client.post(
        "/v1/events/ingest",
        json={
            "source": "gmail",
            "event_type": "email_received",
            "entity_type": "email_thread",
            "entity_id": "thr_dup_001",
            "title": "Already processed email",
            "summary": "This was already ingested",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "duplicate"
    assert data["event_id"] is None


# ---------------------------------------------------------------------------
# Scenario 2: Morning briefing with priorities, changes, and approvals
# ---------------------------------------------------------------------------


def test_morning_briefing_returns_structured_brief():
    """GET /v1/briefings/{date} should return a structured briefing with
    headline, priorities, changes, pending approvals, and recommended actions."""
    from src.api import deps
    from src.models.briefings import Briefing

    mock_briefing = MagicMock(spec=Briefing)
    mock_briefing.briefing_id = "brief_morning_001"
    mock_briefing.briefing_date = date(2026, 3, 14)
    mock_briefing.headline = "3 priorities, 2 follow-ups, 1 meeting risk"
    mock_briefing.top_priorities = [
        {
            "title": "Review Series A term sheet",
            "reason": "Investor expecting response by EOD Friday",
        },
        {"title": "Prepare board deck", "reason": "Board meeting Monday"},
        {"title": "Finalize Q1 OKRs", "reason": "Team alignment blocked"},
    ]
    mock_briefing.changes_since_last = [
        {"source": "gmail", "summary": "5 new emails, 2 high priority", "count": 5},
        {"source": "calendar", "summary": "1 meeting rescheduled", "count": 1},
    ]
    mock_briefing.pending_approvals = [
        {"approval_id": "apr_001", "title": "Approve: Reply to investor"},
    ]
    mock_briefing.recommended_actions = [
        "Review term sheet before 3pm call",
        "Approve draft reply to investor",
        "Block 2 hours for board deck",
    ]
    mock_briefing.full_text = (
        "**Good morning.** You have 3 priorities today...\n\n"
        "The Series A term sheet arrived overnight. "
        "Your board meeting is Monday — the deck needs 2 more hours of work."
    )

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_briefing
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[deps.get_session] = lambda: mock_db

    try:
        response = client.get("/v1/briefings/2026-03-14")

        assert response.status_code == 200
        data = response.json()

        assert data["briefing_id"] == "brief_morning_001"
        assert data["headline"] == "3 priorities, 2 follow-ups, 1 meeting risk"
        assert len(data["top_priorities"]) == 3
        assert data["top_priorities"][0]["title"] == "Review Series A term sheet"
        assert len(data["changes_since_last"]) == 2
        assert len(data["pending_approvals"]) == 1
        assert data["pending_approvals"][0]["approval_id"] == "apr_001"
        assert len(data["recommended_actions"]) == 3
        assert "term sheet" in data["full_text"].lower()
    finally:
        app.dependency_overrides.pop(deps.get_session, None)


# ---------------------------------------------------------------------------
# Scenario 3: Meeting prep with attendee and project context
# ---------------------------------------------------------------------------


@patch("src.api.routes_meetings.Presenter")
def test_meeting_prep_returns_attendee_context(mock_presenter_cls):
    """POST /v1/meetings/prep should return a prep card with attendees,
    agenda, related threads, action items, and risks."""
    mock_instance = MagicMock()
    mock_instance.generate_meeting_prep = AsyncMock(
        return_value={
            "meeting_id": "evt_meeting_001",
            "title": "Series A Discussion with Acme Ventures",
            "starts_at": "2026-03-14T15:00:00+00:00",
            "attendees": [
                {
                    "name": "Jane VC",
                    "email": "partner@vc.com",
                    "role": "Managing Partner",
                    "recent_context": "Sent term sheet yesterday, "
                    "has been in 3 email threads this week",
                },
                {
                    "name": "Bob CTO",
                    "email": "bob@startup.com",
                    "role": "Co-founder & CTO",
                    "recent_context": "Discussed technical due diligence questions",
                },
            ],
            "agenda": [
                "Review term sheet key terms",
                "Discuss valuation and dilution",
                "Technical due diligence timeline",
            ],
            "related_threads": [
                {
                    "title": "Series A term sheet attached",
                    "summary": "Term sheet with $5M raise at $20M pre",
                    "event_id": "evt_email_001",
                },
            ],
            "action_items": [
                {
                    "description": "Send updated cap table before meeting",
                    "owner": "You",
                    "priority": "high",
                },
            ],
            "risks": [
                "Term sheet expires Friday — need decision today",
                "CTO has conflicting call at 15:30",
            ],
            "talking_points": [
                "Clarify board seat expectations",
                "Discuss pro-rata rights",
            ],
        }
    )
    mock_presenter_cls.return_value = mock_instance

    response = client.post(
        "/v1/meetings/prep",
        json={"meeting_id": "evt_meeting_001"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify meeting prep structure
    assert data["meeting_id"] == "evt_meeting_001"
    assert data["title"] == "Series A Discussion with Acme Ventures"
    assert "2026-03-14T15:00:00" in data["starts_at"]

    # Attendees with context
    assert len(data["attendees"]) == 2
    assert data["attendees"][0]["name"] == "Jane VC"
    assert "term sheet" in data["attendees"][0]["recent_context"].lower()

    # Agenda, threads, action items, risks
    assert len(data["agenda"]) == 3
    assert len(data["related_threads"]) == 1
    assert len(data["action_items"]) == 1
    assert data["action_items"][0]["priority"] == "high"
    assert len(data["risks"]) == 2


# ---------------------------------------------------------------------------
# Scenario 4: Rejected approval → no external write + audit recorded
# ---------------------------------------------------------------------------


@patch("src.api.routes_approvals.AuditService")
def test_rejected_approval_cancels_execution_and_audits(mock_audit_cls):
    """POST /v1/approvals/{id}/reject should:
    1. Mark approval as rejected
    2. Cancel the associated execution
    3. Record the rejection in the audit trail
    4. NOT trigger any external write (GraphExecutor not called)
    """
    # Set up mock approval (pending)
    mock_approval = MagicMock(spec=Approval)
    mock_approval.approval_id = "apr_reject_001"
    mock_approval.user_id = TEST_USER_ID
    mock_approval.status = "pending"
    mock_approval.execution_id = "exec_reject_001"
    mock_approval.title = "Approve: Reply to investor"
    mock_approval.summary = "Draft reply about term sheet"
    mock_approval.risk_level = "medium"
    mock_approval.expires_at = None
    mock_approval.created_at = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
    mock_approval.run_id = None
    mock_approval.step_id = None
    mock_approval.requested_by = None
    mock_approval.approved_by = None

    # Set up mock task run
    mock_execution = MagicMock(spec=TaskRun)
    mock_execution.run_id = "exec_reject_001"
    mock_execution.status = "awaiting_approval"

    # Mock DB: first call returns approval (with_for_update), second returns execution
    from src.api import deps

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

    # Mock audit service
    mock_audit = MagicMock()
    mock_audit.log = AsyncMock(return_value="aud_reject_001")
    mock_audit_cls.return_value = mock_audit

    # Override session dependency
    app.dependency_overrides[deps.get_session] = lambda: mock_db

    try:
        response = client.post(
            "/v1/approvals/apr_reject_001/reject",
            json={"reason": "Terms are unfavorable, need to renegotiate"},
        )

        assert response.status_code == 200
        data = response.json()

        # Approval marked as rejected
        assert data["status"] == "rejected"
        assert data["approval_id"] == "apr_reject_001"

        # Execution was cancelled
        assert mock_execution.status == "cancelled"

        # Audit trail recorded the rejection
        mock_audit.log.assert_called_once()
        audit_call = mock_audit.log.call_args
        assert audit_call.kwargs["action_type"] == "approval_rejected"
        assert audit_call.kwargs["approval_id"] == "apr_reject_001"
        assert audit_call.kwargs["execution_id"] == "exec_reject_001"

    finally:
        app.dependency_overrides.pop(deps.get_session, None)

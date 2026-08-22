"""The approvals list can name the prepared queue.

CLAUDE.md: the prepared_work queue is the only place a prepared action can be
acted on. The UI that replaces the surface detail modal's `queue` tab has to
be able to ASK for prepared actions; today the list response does not even
say which type a row is.
"""

from src.api.routes_approvals import ApprovalResponse


def test_the_list_response_states_the_approval_type():
    resp = ApprovalResponse(
        approval_id="apr_1",
        status="pending",
        title="Send the term sheet reply",
        summary=None,
        approval_type="prepared_action",
        risk_level="high",
        created_at=None,
    )
    assert resp.approval_type == "prepared_action"


def test_the_field_is_required_so_a_caller_cannot_silently_lose_it():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ApprovalResponse(
            approval_id="apr_1",
            status="pending",
            title="x",
            summary=None,
            risk_level="high",
            created_at=None,
        )

"""How prepared work reaches the founder — the calm path, and one pointer line.

A prepared action is finished, staged work waiting on the founder's schedule. It is by
construction NOT urgent, so it must never inherit ``approval_request``'s two delivery
behaviours: skipping the priority + rate-limit filters, and broadcasting to every active
surface. Nothing announces a prepared action per item; it is discovered through the standing
workspace queue card and the briefing's one pointer line.

The settled rule these tests lock in: the prepared-work queue is the ONLY place a prepared
action can be acted on. The briefing may point at the queue. It may never re-ask. Two places
to act on one decision is a notification machine; one place plus a pointer is a second mind.
"""

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.deep_runtime.middleware.approval_persistence import PREPARED_APPROVAL_TYPE
from src.models.approvals import Approval
from src.services.notifier import (
    BROADCAST_TYPES,
    BYPASS_FILTER_TYPES,
    bypasses_delivery_filters,
)
from src.services.presenter import Presenter
from tests.conftest import (
    TEST_USER_ID,
    TEST_WORKSPACE_ID,
    make_filtering_db,
    make_mock_settings,
)

# ---------------------------------------------------------------------------
# Notifier — the urgent-delivery constants keep their meaning
# ---------------------------------------------------------------------------


class TestDeliveryFilterConstants:
    """Regression guard: lifting the inline tuples to named module constants changed
    nothing about which types are urgent."""

    def test_approval_request_still_bypasses_and_broadcasts(self):
        for urgent in ("approval_request", "critical_alert", "auto_execute_notify"):
            assert bypasses_delivery_filters(urgent) is True
        assert "approval_request" in BROADCAST_TYPES
        assert "critical_alert" in BROADCAST_TYPES
        # auto_execute_notify bypasses the filters but goes to the preferred surface only.
        assert "auto_execute_notify" not in BROADCAST_TYPES

    def test_ordinary_types_are_unchanged(self):
        for ordinary in ("info_update", "briefing"):
            assert bypasses_delivery_filters(ordinary) is False
            assert ordinary not in BYPASS_FILTER_TYPES
            assert ordinary not in BROADCAST_TYPES


# ---------------------------------------------------------------------------
# Briefing — one pointer line, never a re-ask
# ---------------------------------------------------------------------------

# The capability a prepared row carries, and the exact string that leaks into the briefing
# if prepared rows are rendered per item: their title is f"Approve: {capability}".
LEAKED_CAPABILITY = "email.send"
ORDINARY_APPROVAL_TITLE = "Confirm the quarterly board deck upload"


def _approval(
    approval_id: str,
    *,
    approval_type: str,
    title: str,
    status: str = "pending",
    risk: str = "high",
) -> Approval:
    apr = Approval()
    apr.approval_id = approval_id
    apr.user_id = TEST_USER_ID
    apr.workspace_id = TEST_WORKSPACE_ID
    apr.approval_type = approval_type
    apr.status = status
    apr.title = title
    apr.risk_level = risk
    apr.created_at = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)
    apr.artifact_refs = {
        "prepared": approval_type == PREPARED_APPROVAL_TYPE,
        "capability": LEAKED_CAPABILITY,
        "tool_input": json.dumps({"to": "board@acme.com"}),
    }
    return apr


def _prepared(approval_id: str, *, status: str = "pending") -> Approval:
    """A prepared row shaped exactly as ``approval_persistence`` writes it: pending, titled
    with its capability, carrying the recorded payload in ``artifact_refs``."""
    return _approval(
        approval_id,
        approval_type=PREPARED_APPROVAL_TYPE,
        title=f"Approve: {LEAKED_CAPABILITY}",
        status=status,
    )


def _presenter_with(*rows: Approval) -> Presenter:
    """A Presenter whose approval queries run FOR REAL against a filtering db double.

    ``_get_pending_approvals`` and ``_count_prepared_actions`` are deliberately NOT stubbed.
    The leak assertions below exist to guard the ``approval_type`` filter inside
    ``_get_pending_approvals``; a stub of that method cannot go red when the filter is
    deleted, so the query itself has to be what runs. The double applies the statement's
    real WHERE clause, so deleting a filter changes what the briefing sees.

    Every OTHER collaborator is stubbed explicitly: an unstubbed one would return a
    MagicMock and the test would fail on that rather than on its assertion.
    """
    presenter = Presenter.__new__(Presenter)
    presenter._settings = make_mock_settings()
    presenter._db = make_filtering_db(list(rows))
    presenter._get_recent_events = AsyncMock(return_value=[])
    presenter._get_active_plans = AsyncMock(return_value=[])
    presenter._get_upcoming_meetings = AsyncMock(return_value=[])
    presenter._build_connection_section = AsyncMock(return_value="")
    return presenter


def _ordinary() -> Approval:
    return _approval(
        "apr_ordinary",
        approval_type="approval_request",
        title=ORDINARY_APPROVAL_TITLE,
        risk="low",
    )


class TestBriefingPointer:
    async def test_briefing_gains_exactly_one_pointer_line(self):
        presenter = _presenter_with(
            _prepared("apr_p1"),
            _prepared("apr_p2"),
            _prepared("apr_p3"),
            # Already decided — must count for neither the pointer nor the section.
            _prepared("apr_done", status="approved"),
            _ordinary(),
        )

        context = await presenter._gather_briefing_data(
            TEST_USER_ID, date(2026, 8, 19), workspace_id=TEST_WORKSPACE_ID
        )

        assert context.count("prepared for review") == 1
        assert "3 actions prepared for review" in context

        # The prepared rows are pending approvals too. If the approvals query stops
        # excluding them, each is rendered per item as "Approve: email.send (risk: high)" —
        # the second place to act on one decision that this design forbids. The section must
        # therefore hold the ordinary approval and nothing else.
        assert "## Pending Approvals (1)" in context
        assert ORDINARY_APPROVAL_TITLE in context
        # The briefing is fed to an LLM: anything here the model may expand on. No
        # capability names, no per-item detail — that is what the queue is for.
        assert LEAKED_CAPABILITY not in context

    async def test_briefing_says_nothing_when_the_queue_is_empty(self):
        presenter = _presenter_with(_ordinary())

        context = await presenter._gather_briefing_data(
            TEST_USER_ID, date(2026, 8, 19), workspace_id=TEST_WORKSPACE_ID
        )

        assert "prepared for review" not in context
        assert "prepared" not in context.lower()
        # ...while the ordinary approval it shares a table with still reaches the briefing.
        assert ORDINARY_APPROVAL_TITLE in context

    async def test_the_pointer_line_is_singular_for_one_item(self):
        presenter = _presenter_with(_prepared("apr_p1"))

        context = await presenter._gather_briefing_data(
            TEST_USER_ID, date(2026, 8, 19), workspace_id=TEST_WORKSPACE_ID
        )

        assert "1 action prepared for review" in context
        assert "1 actions" not in context


class TestPreparedRowsStayOutOfTheApprovalSection:
    """The pointer line is only ONE place to act if prepared rows are also kept out of the
    briefing's per-item "Pending Approvals" section.

    A prepared write is persisted as an ``Approval`` with ``status="pending"`` and
    ``title=f"Approve: {capability}"``, so the unfiltered approvals query would otherwise
    render every prepared item back into the briefing with its capability and risk level —
    exactly the re-ask this design forbids.
    """

    def _presenter_capturing_statement(self):
        presenter = Presenter.__new__(Presenter)
        presenter._settings = make_mock_settings()
        captured = {}

        async def execute(stmt):
            captured["stmt"] = stmt
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

        presenter._db = MagicMock()
        presenter._db.execute = execute
        return presenter, captured

    async def test_pending_approvals_query_excludes_prepared_rows(self):
        presenter, captured = self._presenter_capturing_statement()

        await presenter._get_pending_approvals(TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID)

        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        assert f"approval_type != '{PREPARED_APPROVAL_TYPE}'" in sql

    async def test_prepared_count_query_targets_prepared_pending_rows(self):
        presenter, captured = self._presenter_capturing_statement()

        async def execute(stmt):
            captured["stmt"] = stmt
            result = MagicMock()
            result.scalar.return_value = 2
            return result

        presenter._db.execute = execute

        count = await presenter._count_prepared_actions(
            TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
        )

        assert count == 2
        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        assert f"approval_type = '{PREPARED_APPROVAL_TYPE}'" in sql
        assert "status = 'pending'" in sql
        assert f"user_id = '{TEST_USER_ID}'" in sql
        assert f"workspace_id = '{TEST_WORKSPACE_ID}'" in sql
        assert "count(" in sql.lower()

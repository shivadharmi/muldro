"""How prepared work reaches the founder — the calm path, and one pointer line.

A prepared action is finished, staged work waiting on the founder's schedule. It is by
construction NOT urgent, so the two delivery behaviours it must never inherit from
``approval_request`` are:

  1. skipping the priority + rate-limit filters, and
  2. broadcasting to every active surface.

The settled rule these tests lock in: the prepared-work queue is the ONLY place a prepared
action can be acted on. The briefing may point at the queue. It may never re-ask. Two places
to act on one decision is a notification machine; one place plus a pointer is a second mind.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.deep_runtime.middleware.approval_persistence import PREPARED_APPROVAL_TYPE
from src.services.notifier import (
    BROADCAST_TYPES,
    BYPASS_FILTER_TYPES,
    PREPARED_WORK_NOTIFICATION_TYPE,
    Notifier,
    bypasses_delivery_filters,
)
from src.services.presenter import Presenter
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

# ---------------------------------------------------------------------------
# Notifier — prepared work flows through the calm path
# ---------------------------------------------------------------------------


class TestPreparedWorkDeliveryPolicy:
    def test_prepared_work_does_not_bypass_the_delivery_filters(self):
        """Prepared work earns delivery on its priority score like any other signal."""
        assert bypasses_delivery_filters(PREPARED_WORK_NOTIFICATION_TYPE) is False
        assert PREPARED_WORK_NOTIFICATION_TYPE not in BYPASS_FILTER_TYPES
        assert PREPARED_WORK_NOTIFICATION_TYPE not in BROADCAST_TYPES

    def test_approval_request_still_bypasses_and_broadcasts(self):
        """Regression guard: lifting the tuples to module level changed nothing."""
        for urgent in ("approval_request", "critical_alert", "auto_execute_notify"):
            assert bypasses_delivery_filters(urgent) is True
        assert "approval_request" in BROADCAST_TYPES
        assert "critical_alert" in BROADCAST_TYPES
        # auto_execute_notify bypasses the filters but goes to the preferred surface only.
        assert "auto_execute_notify" not in BROADCAST_TYPES

    def test_ordinary_types_are_unchanged(self):
        for ordinary in ("info_update", "briefing"):
            assert bypasses_delivery_filters(ordinary) is False
            assert ordinary not in BROADCAST_TYPES


class TestPreparedWorkNotifyBehaviour:
    """The constants above are only meaningful if ``notify`` actually honours them."""

    def _notifier(self):
        registry = AsyncMock()
        registry.get_active_surfaces = AsyncMock(return_value=["web", "slack"])
        registry.get_preferred_surface = AsyncMock(return_value="web")
        return Notifier(surface_registry=registry)

    async def test_low_priority_prepared_work_holds_for_the_briefing(self):
        notifier = self._notifier()
        hold = AsyncMock()
        deliver = AsyncMock(return_value={"status": "published"})

        with (
            patch.object(notifier, "_hold_for_briefing", hold),
            patch.object(notifier, "_deliver", deliver),
        ):
            result = await notifier.notify(
                user_id=TEST_USER_ID,
                notification_type=PREPARED_WORK_NOTIFICATION_TYPE,
                title="Prepared: email.send",
                body="Waiting in the review queue",
                # Score 0.47 — inside the [0.3, 0.6) hold band, where an ordinary signal
                # waits for the briefing rather than interrupting.
                data={"urgency": 0.4},
            )

        assert result["status"] == "held_for_briefing"
        hold.assert_awaited_once()
        deliver.assert_not_awaited()

    async def test_high_priority_prepared_work_never_broadcasts(self):
        notifier = self._notifier()
        deliver = AsyncMock(return_value={"status": "published"})

        with (
            patch.object(notifier, "_deliver", deliver),
            patch.object(notifier, "_mark_delivered", AsyncMock()),
        ):
            result = await notifier.notify(
                user_id=TEST_USER_ID,
                notification_type=PREPARED_WORK_NOTIFICATION_TYPE,
                title="Prepared: email.send",
                body="Waiting in the review queue",
                data={
                    "urgency": 1.0,
                    "goal_relevance": 1.0,
                    "novelty": 1.0,
                    "confidence": 1.0,
                    "interruptibility": 1.0,
                },
            )

        assert result["status"] == "sent"
        assert list(result["surfaces"]) == ["web"]
        deliver.assert_awaited_once()


# ---------------------------------------------------------------------------
# Briefing — one pointer line, never a re-ask
# ---------------------------------------------------------------------------

LEAKED_CAPABILITY = "email.send"


def _presenter_with(prepared_count: int) -> Presenter:
    """A Presenter double stubbed down to exactly what ``_gather_briefing_data`` calls.

    Every collaborator is stubbed explicitly: an unstubbed one would return a MagicMock and
    the test would fail on that rather than on its assertion.
    """
    presenter = Presenter.__new__(Presenter)
    presenter._settings = make_mock_settings()
    presenter._db = MagicMock()
    presenter._count_prepared_actions = AsyncMock(return_value=prepared_count)
    presenter._get_recent_events = AsyncMock(return_value=[])
    presenter._get_active_plans = AsyncMock(return_value=[])
    presenter._get_pending_approvals = AsyncMock(return_value=[])
    presenter._get_upcoming_meetings = AsyncMock(return_value=[])
    presenter._build_connection_section = AsyncMock(return_value="")
    return presenter


class TestBriefingPointer:
    async def test_briefing_gains_exactly_one_pointer_line(self):
        presenter = _presenter_with(3)

        context = await presenter._gather_briefing_data(
            TEST_USER_ID, date(2026, 8, 19), workspace_id=TEST_WORKSPACE_ID
        )

        assert context.count("prepared for review") == 1
        assert "3 actions prepared for review" in context
        # The briefing is fed to an LLM: anything here the model may expand on. No payloads,
        # no capability names, no per-item detail — that is what the queue is for.
        assert "tool_input" not in context
        assert LEAKED_CAPABILITY not in context

    async def test_briefing_says_nothing_when_the_queue_is_empty(self):
        presenter = _presenter_with(0)

        context = await presenter._gather_briefing_data(
            TEST_USER_ID, date(2026, 8, 19), workspace_id=TEST_WORKSPACE_ID
        )

        assert "prepared for review" not in context
        assert "prepared" not in context.lower()

    async def test_the_pointer_line_is_singular_for_one_item(self):
        presenter = _presenter_with(1)

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


@pytest.mark.parametrize(
    "notification_type",
    ["approval_request", "critical_alert", "auto_execute_notify", "info_update", "briefing"],
)
def test_prepared_work_type_is_distinct_from_every_existing_type(notification_type):
    assert PREPARED_WORK_NOTIFICATION_TYPE != notification_type

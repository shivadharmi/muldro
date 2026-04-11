"""Tests for EngagementService — suppression rules, rate calculation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.engagement_service import EngagementService


def _make_mock_db():
    """Create a mock AsyncSession with execute/commit/add."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _make_history_row(
    engaged=0,
    dismissed=0,
    ignored=0,
    consecutive_dismissals=0,
    suppressed=False,
):
    """Create a mock EngagementHistory row."""
    row = MagicMock()
    row.engaged_count = engaged
    row.dismissed_count = dismissed
    row.ignored_count = ignored
    row.consecutive_dismissals = consecutive_dismissals
    row.engagement_rate = engaged / max(engaged + dismissed + ignored, 1)
    row.suppressed = suppressed
    row.last_engaged_at = None
    row.last_dismissed_at = None
    return row


@pytest.mark.asyncio
async def test_record_engagement_resets_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(engaged=2, dismissed=3, consecutive_dismissals=3)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "engaged")

    assert row.consecutive_dismissals == 0
    assert row.engaged_count == 3
    assert row.suppressed is False


@pytest.mark.asyncio
async def test_record_dismissal_increments_consecutive():
    db = _make_mock_db()
    row = _make_history_row(dismissed=2, consecutive_dismissals=2)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "dismissed")

    assert row.consecutive_dismissals == 3
    assert row.dismissed_count == 3


@pytest.mark.asyncio
async def test_suppression_at_5_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=4, consecutive_dismissals=4)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "dismissed")

    assert row.consecutive_dismissals == 5
    assert row.suppressed is True


@pytest.mark.asyncio
async def test_engagement_on_suppressed_removes_suppression():
    db = _make_mock_db()
    row = _make_history_row(engaged=0, dismissed=5, consecutive_dismissals=5, suppressed=True)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "engaged")

    assert row.suppressed is False
    assert row.consecutive_dismissals == 0


@pytest.mark.asyncio
async def test_relevance_penalty_at_3_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=3, consecutive_dismissals=3)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_no_penalty_below_3_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=1, consecutive_dismissals=1)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == 0.0


@pytest.mark.asyncio
async def test_suppressed_source_returns_full_penalty():
    db = _make_mock_db()
    row = _make_history_row(dismissed=5, consecutive_dismissals=5, suppressed=True)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == 1.0


@pytest.mark.asyncio
async def test_creates_new_row_on_first_engagement():
    db = _make_mock_db()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("github", "pr_review", "engaged")

    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_engagement_context_returns_formatted_context():
    db = _make_mock_db()
    svc = EngagementService(db, "ws_test")

    row1 = _make_history_row(dismissed=3, consecutive_dismissals=3)
    row1.signal_source = "gmail"
    row1.signal_category = "reply"
    row1.suppressed = False
    row1.engagement_rate = 0.2

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row1]
    db.execute.return_value = result_mock

    context = await svc.get_engagement_context()
    assert "gmail" in context
    assert "reply" in context

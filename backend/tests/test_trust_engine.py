"""Tests for TrustEngine — graduated autonomy scoring."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.trust_engine import TrustEngine


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def engine(mock_db):
    return TrustEngine(mock_db)


def _make_trust_score(
    action_type="send_email",
    approved=10,
    rejected=0,
    score=1.0,
    threshold=0.8,
):
    ts = MagicMock()
    ts.user_id = "usr_default"
    ts.action_type = action_type
    ts.approved_count = approved
    ts.rejected_count = rejected
    ts.trust_score = score
    ts.auto_approve_threshold = threshold
    ts.last_decision_at = datetime.now(timezone.utc)
    return ts


class TestRecordDecision:
    async def test_increments_approved(self, engine, mock_db):
        ts = _make_trust_score(approved=5, rejected=0, score=1.0)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        score = await engine.record_decision("usr_default", "send_email", approved=True)
        assert ts.approved_count == 6
        assert score == 6 / 6  # 1.0

    async def test_increments_rejected(self, engine, mock_db):
        ts = _make_trust_score(approved=5, rejected=0, score=1.0)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        score = await engine.record_decision("usr_default", "send_email", approved=False)
        assert ts.rejected_count == 1
        assert score == 5 / 6


class TestShouldAutoApprove:
    async def test_auto_approve_when_trusted(self, engine, mock_db):
        ts = _make_trust_score(approved=10, rejected=0, score=1.0, threshold=0.8)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await engine.should_auto_approve("usr_default", "send_email") is True

    async def test_no_auto_approve_high_risk(self, engine, mock_db):
        ts = _make_trust_score(approved=10, rejected=0, score=1.0)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await engine.should_auto_approve("usr_default", "send_email", "high") is False

    async def test_no_auto_approve_insufficient_history(self, engine, mock_db):
        ts = _make_trust_score(approved=2, rejected=0, score=1.0)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await engine.should_auto_approve("usr_default", "send_email") is False

    async def test_no_auto_approve_below_threshold(self, engine, mock_db):
        ts = _make_trust_score(approved=4, rejected=4, score=0.5, threshold=0.8)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await engine.should_auto_approve("usr_default", "send_email") is False

    async def test_no_trust_score_returns_false(self, engine, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await engine.should_auto_approve("usr_default", "send_email") is False


class TestGetTrustDashboard:
    async def test_returns_all_scores(self, engine, mock_db):
        ts1 = _make_trust_score("send_email", approved=10, rejected=1, score=0.91)
        ts2 = _make_trust_score("create_event", approved=5, rejected=0, score=1.0)

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ts2, ts1]
        mock_db.execute = AsyncMock(return_value=result_mock)

        dashboard = await engine.get_trust_dashboard("usr_default")
        assert len(dashboard) == 2
        assert dashboard[0]["action_type"] == "create_event"


class TestResetTrust:
    async def test_resets_specific_action(self, engine, mock_db):
        ts = _make_trust_score(approved=10, rejected=2, score=0.83)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ts
        mock_db.execute = AsyncMock(return_value=result_mock)

        await engine.reset_trust("usr_default", "send_email")
        assert ts.approved_count == 0
        assert ts.rejected_count == 0
        assert ts.trust_score == 0.0

    async def test_resets_all(self, engine, mock_db):
        ts1 = _make_trust_score("a", approved=5, rejected=1, score=0.83)
        ts2 = _make_trust_score("b", approved=3, rejected=0, score=1.0)

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ts1, ts2]
        mock_db.execute = AsyncMock(return_value=result_mock)

        await engine.reset_trust("usr_default")
        assert ts1.approved_count == 0
        assert ts2.approved_count == 0

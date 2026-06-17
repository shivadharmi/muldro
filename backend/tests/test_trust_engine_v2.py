"""Tests for rewritten TrustEngine — deterministic 4×4 matrix."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import RiskAssessment
from src.services.trust_engine import TrustEngine, _graduation_progress


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def engine(mock_db):
    return TrustEngine(mock_db, workspace_id="ws_test")


def _make_trust_state(trust_level="first_use"):
    s = MagicMock()
    s.trust_level = trust_level
    s.approved_count = 0
    s.rejected_count = 0
    s.modified_count = 0
    s.cooldown_until = None
    return s


def _make_ceiling(max_level="autonomous"):
    c = MagicMock()
    c.max_level = max_level
    return c


def _make_risk(risk_level="low", reasoning="test"):
    return RiskAssessment(risk_level=risk_level, reasoning=reasoning)


def test_default_ceiling_is_frozen_and_autonomous():
    """_get_ceiling's no-row default is a typed, immutable autonomous ceiling (SVC-P3-2)."""
    import dataclasses

    from src.services.trust_engine import _DefaultCeiling

    ceiling = _DefaultCeiling()
    assert ceiling.max_level == "autonomous"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ceiling.max_level = "first_use"  # type: ignore[misc]


class TestEvaluateFirstUse:
    """first_use × any risk → approval_required."""

    async def test_first_use_none_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("first_use"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "approval_required"

    async def test_first_use_high_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("first_use"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestEvaluateLearning:
    """learning × any risk → approval_required."""

    async def test_learning_low_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("learning"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "approval_required"


class TestEvaluateTrusted:
    """trusted × none/low → auto_execute_notify; trusted × medium/high → approval_required."""

    async def test_trusted_none_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "auto_execute_notify"

    async def test_trusted_low_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "auto_execute_notify"

    async def test_trusted_medium_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("medium"))
        assert result.decision == "approval_required"

    async def test_trusted_high_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestEvaluateAutonomous:
    """autonomous: none/low → silent, medium → notify, high → approval_required."""

    async def test_autonomous_none_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "auto_execute_silent"

    async def test_autonomous_low_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "auto_execute_silent"

    async def test_autonomous_medium_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("medium"))
        assert result.decision == "auto_execute_notify"

    async def test_autonomous_high_risk(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestGraduationProgress:
    def test_blocked_caps_percentage(self):
        """M-25: blocked_by_rejections should cap percentage below 1.0."""
        state = SimpleNamespace(
            trust_level="learning",
            approved_count=15,
            rejected_count=3,  # 3/18 = 16.7% >= 10%
        )
        progress = _graduation_progress(state)
        assert progress["blocked_by_rejections"] is True
        assert progress["percentage"] < 1.0
        assert progress["status"] == "blocked_by_rejections"

    def test_not_blocked_no_cap(self):
        """M-25: when not blocked, percentage can reach 1.0."""
        state = SimpleNamespace(
            trust_level="learning",
            approved_count=15,
            rejected_count=1,  # 1/16 = 6.25% < 10%
        )
        progress = _graduation_progress(state)
        assert progress["blocked_by_rejections"] is False
        assert progress["percentage"] == 1.0
        assert progress.get("status") is None

    def test_blocked_at_first_use(self):
        """Blocked at first_use caps percentage."""
        state = SimpleNamespace(
            trust_level="first_use",
            approved_count=2,
            rejected_count=1,
        )
        progress = _graduation_progress(state)
        assert progress["blocked_by_rejections"] is True
        assert progress["percentage"] <= 0.95
        assert progress["status"] == "blocked_by_rejections"


class TestCeilingRespected:
    """Ceiling caps effective trust level."""

    async def test_ceiling_caps_autonomous_to_trusted(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("trusted"))
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "auto_execute_notify"

    async def test_ceiling_caps_trusted_to_learning(self, engine):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("learning"))
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "approval_required"


class TestEvaluateReturnsTrustFields:
    """evaluate() must populate trust_level, effective_trust_level, counters."""

    async def test_first_use_returns_trust_fields(self, engine):
        state = _make_trust_state("first_use")
        state.approved_count = 2
        state.rejected_count = 1
        engine._get_trust_state = AsyncMock(return_value=state)
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("autonomous"))

        result = await engine.evaluate("email.send", _make_risk("low"))

        assert result.trust_level == "first_use"
        assert result.effective_trust_level == "first_use"
        assert result.approved_count == 2
        assert result.rejected_count == 1

    async def test_ceiling_limits_effective_level(self, engine):
        state = _make_trust_state("trusted")
        state.approved_count = 15
        state.rejected_count = 0
        engine._get_trust_state = AsyncMock(return_value=state)
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("learning"))

        result = await engine.evaluate("email.send", _make_risk("low"))

        assert result.trust_level == "trusted"
        assert result.effective_trust_level == "learning"
        assert result.approved_count == 15
        assert result.rejected_count == 0

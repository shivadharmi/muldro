"""Tests for rewritten TrustEngine — deterministic 4×4 matrix."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import RiskAssessment
from src.services.trust_engine import TrustEngine


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

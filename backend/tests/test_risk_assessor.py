"""Tests for LLM risk assessor + Redis caching."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.risk_assessor import (
    RiskAssessment,
    assess_risk,
    build_risk_cache_key,
    get_or_assess_risk,
    graduate_trust,
)

_LOW_RISK_JSON = json.dumps(
    {
        "risk_level": "low",
        "reasoning": "Casual lunch message to known contact",
        "reversible": True,
        "blast_radius": "external_single",
    }
)


@pytest.fixture
def mock_complete():
    """Patch the UtilityLLM seam to return a low-risk JSON string."""
    with patch(
        "src.services.risk_assessor.complete_text",
        AsyncMock(return_value=_LOW_RISK_JSON),
    ) as m:
        yield m


class TestGraduateTrust:
    def test_high_count_moderate_rejections_stays_learning(self):
        """SVC-P1-1: 25+ approved with 10-15% rejection stays learning.

        The old undocumented `25+ and <15% -> trusted` branch let a frequently
        rejected capability keep auto-executing (and re-escape demotion after
        each cooldown). Removed: only <10% earns trusted, <5% earns autonomous.
        """
        state = SimpleNamespace(
            approved_count=25,
            rejected_count=3,  # ~10.7% rejection (>= 10% trusted cap)
            cooldown_until=None,
            trust_level="learning",
        )
        assert graduate_trust(state) == "learning"

    def test_high_count_high_rejections_stays_learning(self):
        """H-4: 25+ approved with >=15% rejection stays learning."""
        state = SimpleNamespace(
            approved_count=25,
            rejected_count=5,  # ~16.7% rejection
            cooldown_until=None,
            trust_level="learning",
        )
        assert graduate_trust(state) == "learning"

    def test_25_approved_low_rejection_is_autonomous(self):
        """Baseline: 25+ approved with <5% rejection -> autonomous."""
        state = SimpleNamespace(
            approved_count=25,
            rejected_count=1,  # ~3.8%
            cooldown_until=None,
            trust_level="trusted",
        )
        assert graduate_trust(state) == "autonomous"

    def test_10_approved_high_rejection_stays_learning(self):
        """10+ approved with rejection rate >= 10% stays learning (not trusted).

        (Data is 2/12 ≈ 16.7%. The exact-10% strict-boundary case is pinned in
        test_trust_graduation.test_exact_ten_percent_rejection_stays_learning.)
        """
        state = SimpleNamespace(
            approved_count=10,
            rejected_count=2,  # 2/12 ≈ 16.7% >= 10%
            cooldown_until=None,
            trust_level="first_use",
        )
        assert graduate_trust(state) == "learning"


class TestRiskAssessment:
    def test_model_validation(self):
        ra = RiskAssessment(
            risk_level="low",
            reasoning="test",
            reversible=True,
            blast_radius="self",
        )
        assert ra.risk_level == "low"

    def test_model_defaults(self):
        ra = RiskAssessment(risk_level="medium", reasoning="test")
        assert ra.reversible is True
        assert ra.blast_radius == "self"


class TestAssessRisk:
    async def test_returns_risk_assessment(self, mock_complete):
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "friend@example.com", "body": "Hey lunch?"},
            user_context={"relationships": {"friend@example.com": "close friend"}},
        )
        assert isinstance(result, RiskAssessment)
        assert result.risk_level == "low"
        mock_complete.assert_awaited_once()

    async def test_falls_back_on_api_error(self, mock_complete):
        # Fail closed (SVC-P2-1): an assessment outage must default to 'high', which
        # maps to approval_required at every trust level — never auto-execute a write.
        mock_complete.side_effect = Exception("API down")
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "ceo@corp.com", "body": "Revenue report"},
            user_context={},
        )
        assert result.risk_level == "high"
        assert "fallback" in result.reasoning.lower() or "failed" in result.reasoning.lower()

    async def test_falls_back_on_invalid_json(self, mock_complete):
        mock_complete.return_value = "not json"
        result = await assess_risk(
            capability="email.send",
            step_input={},
            user_context={},
        )
        # Fail closed (SVC-P2-1): unparseable LLM output → high, not medium.
        assert result.risk_level == "high"


class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        k2 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        assert k1 == k2

    def test_different_targets_different_keys(self):
        k1 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        k2 = build_risk_cache_key("email.send", {"to": "x@y.com", "body": "hi"})
        assert k1 != k2


class TestGetOrAssessRisk:
    async def test_cache_hit(self, mock_complete):
        cached = RiskAssessment(
            risk_level="low", reasoning="cached", reversible=True, blast_radius="self"
        )
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached.model_dump_json())

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            redis=redis,
        )
        assert result.reasoning == "cached"
        mock_complete.assert_not_awaited()

    async def test_cache_miss_calls_llm(self, mock_complete):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            redis=redis,
        )
        assert result.risk_level == "low"
        redis.setex.assert_called_once()
        # Verify 24h TTL
        call_args = redis.setex.call_args
        assert call_args[0][1] == 86400

    async def test_cache_error_falls_through(self, mock_complete):
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=Exception("Redis down"))
        redis.setex = AsyncMock(side_effect=Exception("Redis down"))

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            redis=redis,
        )
        assert result.risk_level == "low"  # LLM still works

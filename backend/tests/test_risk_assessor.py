"""Tests for LLM risk assessor + Redis caching."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import (
    RiskAssessment,
    assess_risk,
    build_risk_cache_key,
    get_or_assess_risk,
)


@pytest.fixture
def mock_client():
    client = AsyncMock()
    response = MagicMock()
    response.content = [
        MagicMock(
            text=json.dumps(
                {
                    "risk_level": "low",
                    "reasoning": "Casual lunch message to known contact",
                    "reversible": True,
                    "blast_radius": "external_single",
                }
            )
        )
    ]
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    client.messages.create = AsyncMock(return_value=response)
    return client


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
    async def test_returns_risk_assessment(self, mock_client):
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "friend@example.com", "body": "Hey lunch?"},
            user_context={"relationships": {"friend@example.com": "close friend"}},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert isinstance(result, RiskAssessment)
        assert result.risk_level == "low"
        mock_client.messages.create.assert_called_once()

    async def test_falls_back_on_api_error(self, mock_client):
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "ceo@corp.com", "body": "Revenue report"},
            user_context={},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "medium"
        assert "fallback" in result.reasoning.lower() or "failed" in result.reasoning.lower()

    async def test_falls_back_on_invalid_json(self, mock_client):
        response = MagicMock()
        response.content = [MagicMock(text="not json")]
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create = AsyncMock(return_value=response)

        result = await assess_risk(
            capability="email.send",
            step_input={},
            user_context={},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "medium"


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
    async def test_cache_hit(self, mock_client):
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
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.reasoning == "cached"
        mock_client.messages.create.assert_not_called()

    async def test_cache_miss_calls_llm(self, mock_client):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "low"
        redis.setex.assert_called_once()
        # Verify 24h TTL
        call_args = redis.setex.call_args
        assert call_args[0][1] == 86400

    async def test_cache_error_falls_through(self, mock_client):
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=Exception("Redis down"))
        redis.setex = AsyncMock(side_effect=Exception("Redis down"))

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "low"  # LLM still works

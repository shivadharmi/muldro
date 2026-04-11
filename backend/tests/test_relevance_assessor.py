"""Tests for the relevance assessor: tier logic and LLM call."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestDetermineTier:
    """Test the pure _determine_tier() function."""

    def test_high_relevance_immediate_urgency_returns_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.8, urgency="immediate")
        assert tier == "push"

    def test_high_relevance_today_urgency_returns_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.7, urgency="today")
        assert tier == "push"

    def test_medium_relevance_today_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.5, urgency="today")
        assert tier == "briefing"

    def test_medium_relevance_this_week_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.4, urgency="this_week")
        assert tier == "briefing"

    def test_medium_relevance_whenever_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.45, urgency="whenever")
        assert tier == "briefing"

    def test_low_relevance_returns_silent(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.2, urgency="immediate")
        assert tier == "silent"

    def test_boundary_0_7_immediate_is_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.7, urgency="immediate")
        assert tier == "push"

    def test_boundary_0_4_whenever_is_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.4, urgency="whenever")
        assert tier == "briefing"

    def test_boundary_0_39_is_silent(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.39, urgency="today")
        assert tier == "silent"

    def test_high_relevance_whenever_is_briefing_not_push(self):
        """relevance >= 0.7 but urgency=whenever → briefing, not push."""
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.9, urgency="whenever")
        assert tier == "briefing"

    def test_high_relevance_this_week_is_briefing_not_push(self):
        """relevance >= 0.7 but urgency=this_week → briefing, not push."""
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.8, urgency="this_week")
        assert tier == "briefing"


class TestAssessRelevance:
    """Test the assess_relevance() async function with mocked Haiku."""

    @pytest.mark.asyncio
    async def test_returns_assessment_from_llm_response(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[
                MagicMock(
                    text='{"relevance_score": 0.8, "reasoning": "PR from key collaborator",'
                    ' "relates_to_goals": ["ship v2"], "urgency": "today",'
                    ' "suggested_actions": []}'
                )
            ]
        )
        signal = PerceptionSignal(
            source="github",
            event_type="pr_review_requested",
            summary="PR #42 review requested by Alice",
        )
        context = UserContext(goals=["ship v2 by Friday"])
        result = await assess_relevance(signal, context, mock_client)
        assert result.relevance_score == 0.8
        assert result.notification_tier == "push"  # 0.8 + today = push
        assert result.urgency == "today"
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_silent_on_llm_error(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = Exception("API error")
        signal = PerceptionSignal(source="gmail", event_type="new_email", summary="Newsletter")
        context = UserContext()
        result = await assess_relevance(signal, context, mock_client)
        assert result.relevance_score == 0.0
        assert result.notification_tier == "silent"

    @pytest.mark.asyncio
    async def test_returns_silent_on_malformed_json(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not json at all")]
        )
        signal = PerceptionSignal(source="slack", event_type="message", summary="Hey")
        context = UserContext()
        result = await assess_relevance(signal, context, mock_client)
        assert result.relevance_score == 0.0
        assert result.notification_tier == "silent"

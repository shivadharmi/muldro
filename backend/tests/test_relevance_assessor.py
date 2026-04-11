"""Tests for the relevance assessor: tier logic and LLM call."""


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

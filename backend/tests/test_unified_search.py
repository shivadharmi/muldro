"""Tests for UnifiedSearchService result model."""

from src.services.unified_search import UnifiedSearchResult


class TestUnifiedSearchResult:
    def test_create_result(self):
        r = UnifiedSearchResult(
            result_type="entity",
            result_id="ent_123",
            title="John Doe",
            snippet="CEO of Acme",
            score=0.9,
            why_matched="name match",
        )
        assert r.result_type == "entity"
        assert r.title == "John Doe"
        assert r.score == 0.9

    def test_to_dict(self):
        r = UnifiedSearchResult(
            result_type="briefing",
            result_id="brf_456",
            title="Daily Briefing",
            why_matched="content match",
            actions=[{"action": "open", "url": "/briefings/brf_456"}],
            metadata={"status": "active"},
        )
        d = r.to_dict()
        assert d["result_type"] == "briefing"
        assert d["title"] == "Daily Briefing"
        assert len(d["actions"]) == 1
        assert d["metadata"]["status"] == "active"

    def test_default_values(self):
        r = UnifiedSearchResult(
            result_type="memory",
            result_id="mem_789",
            title="Some memory",
        )
        assert r.snippet == ""
        assert r.score == 0.0
        assert r.actions == []
        assert r.metadata == {}

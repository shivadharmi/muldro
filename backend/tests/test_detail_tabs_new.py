"""Tests for new detail tab builders added in Phase 4."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_surface(surface_id="surf_test", surface_type="summary", payload=None):
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = surface_type
    s.payload = payload or {}
    s.workspace_id = "ws_test"
    return s


class TestInsightBuilders:
    @pytest.mark.asyncio
    async def test_build_insight_signal_with_data(self):
        from src.services.surface_detail_builders import build_insight_signal

        surface = _mock_surface(
            surface_type="proactive_insight",
            payload={
                "insight_data": {
                    "signal_source": "gmail",
                    "signal_summary": "New email from investor",
                    "relevance_score": 0.85,
                    "relevance_reasoning": "Matches fundraising goal",
                }
            },
        )
        result = await build_insight_signal(AsyncMock(), surface)
        assert result.tab_id == "signal"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_insight_signal_empty(self):
        from src.services.surface_detail_builders import build_insight_signal

        surface = _mock_surface(surface_type="proactive_insight", payload={})
        result = await build_insight_signal(AsyncMock(), surface)
        assert result.tab_id == "signal"

    @pytest.mark.asyncio
    async def test_build_insight_actions(self):
        from src.services.surface_detail_builders import build_insight_actions

        surface = _mock_surface(
            surface_type="proactive_insight",
            payload={
                "insight_data": {
                    "suggested_actions": [
                        {
                            "description": "Reply",
                            "capability": "email.send",
                            "action_input": {},
                            "action_preview": "",
                        },
                    ]
                }
            },
        )
        result = await build_insight_actions(AsyncMock(), surface)
        assert result.tab_id == "actions"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_insight_context_with_goals(self):
        from src.services.surface_detail_builders import build_insight_context

        surface = _mock_surface(
            payload={"insight_data": {"related_goals": ["Close Series A", "Hire CTO"]}}
        )
        result = await build_insight_context(AsyncMock(), surface)
        assert result.tab_id == "context"
        assert len(result.sections) > 0


class TestComparisonBuilders:
    @pytest.mark.asyncio
    async def test_build_comparison_options_with_data(self):
        from src.services.surface_detail_builders import build_comparison_options

        surface = _mock_surface(
            payload={
                "surface_data": {
                    "options": [
                        {
                            "name": "Option A",
                            "description": "Fast",
                            "pros": ["Speed"],
                            "cons": ["Cost"],
                        },
                        {
                            "name": "Option B",
                            "description": "Cheap",
                            "pros": ["Price"],
                            "cons": ["Slow"],
                        },
                    ]
                }
            }
        )
        result = await build_comparison_options(AsyncMock(), surface)
        assert result.tab_id == "options"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_comparison_options_fallback(self):
        from src.services.surface_detail_builders import build_comparison_options

        surface = _mock_surface(payload={"response_preview": "Some text"})
        result = await build_comparison_options(AsyncMock(), surface)
        assert result.tab_id == "options"

    @pytest.mark.asyncio
    async def test_build_comparison_criteria_with_data(self):
        from src.services.surface_detail_builders import build_comparison_criteria

        surface = _mock_surface(
            payload={"surface_data": {"criteria": ["Speed", "Cost", "Reliability"]}}
        )
        result = await build_comparison_criteria(AsyncMock(), surface)
        assert result.tab_id == "criteria"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_comparison_criteria_empty(self):
        from src.services.surface_detail_builders import build_comparison_criteria

        surface = _mock_surface(payload={})
        result = await build_comparison_criteria(AsyncMock(), surface)
        assert result.tab_id == "criteria"


class TestActivityBuilders:
    @pytest.mark.asyncio
    async def test_build_activity_runs_no_workspace(self):
        from src.services.surface_detail_builders import build_activity_runs

        surface = _mock_surface()
        surface.workspace_id = None
        result = await build_activity_runs(AsyncMock(), surface)
        assert result.tab_id == "runs"

    @pytest.mark.asyncio
    async def test_build_activity_stats_no_workspace(self):
        from src.services.surface_detail_builders import build_activity_stats

        surface = _mock_surface()
        surface.workspace_id = None
        result = await build_activity_stats(AsyncMock(), surface)
        assert result.tab_id == "stats"


class TestChecklistBuilders:
    @pytest.mark.asyncio
    async def test_build_checklist_items_with_data(self):
        from src.services.surface_detail_builders import build_checklist_items

        surface = _mock_surface(
            payload={
                "surface_data": {
                    "items": [
                        {"title": "Task 1", "status": "completed"},
                        {"title": "Task 2", "status": "pending"},
                    ]
                }
            }
        )
        result = await build_checklist_items(AsyncMock(), surface)
        assert result.tab_id == "items"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_checklist_items_no_data(self):
        from src.services.surface_detail_builders import build_checklist_items

        surface = _mock_surface(payload={})
        result = await build_checklist_items(AsyncMock(), surface)
        assert result.tab_id == "items"


class TestTableBuilders:
    @pytest.mark.asyncio
    async def test_build_table_data_with_data(self):
        from src.services.surface_detail_builders import build_table_data

        surface = _mock_surface(
            payload={
                "surface_data": {
                    "columns": [{"key": "name", "label": "Name"}],
                    "rows": [{"name": "Row 1"}, {"name": "Row 2"}],
                }
            }
        )
        result = await build_table_data(AsyncMock(), surface)
        assert result.tab_id == "data"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_table_data_fallback(self):
        from src.services.surface_detail_builders import build_table_data

        surface = _mock_surface(payload={"response_preview": "Fallback text"})
        result = await build_table_data(AsyncMock(), surface)
        assert result.tab_id == "data"

    @pytest.mark.asyncio
    async def test_build_table_sources_no_run(self):
        from src.services.surface_detail_builders import build_table_sources

        surface = _mock_surface(payload={})
        result = await build_table_sources(AsyncMock(), surface)
        assert result.tab_id == "sources"


class TestTimelineBuilders:
    @pytest.mark.asyncio
    async def test_build_timeline_events_with_data(self):
        from src.services.surface_detail_builders import build_timeline_events

        surface = _mock_surface(
            payload={
                "surface_data": {
                    "events": [
                        {"label": "Event 1", "timestamp": "2026-04-13"},
                        {"label": "Event 2", "timestamp": "2026-04-12"},
                    ]
                }
            }
        )
        result = await build_timeline_events(AsyncMock(), surface)
        assert result.tab_id == "events"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_timeline_context_no_run(self):
        from src.services.surface_detail_builders import build_timeline_context

        surface = _mock_surface(payload={})
        result = await build_timeline_context(AsyncMock(), surface)
        assert result.tab_id == "context"


class TestAlertDiagnostics:
    @pytest.mark.asyncio
    async def test_build_alert_diagnostics_no_run(self):
        from src.services.surface_detail_builders import build_alert_diagnostics

        surface = _mock_surface(payload={})
        result = await build_alert_diagnostics(AsyncMock(), surface)
        assert result.tab_id == "diagnostics"


class TestRecommendationEvidence:
    @pytest.mark.asyncio
    async def test_build_recommendation_evidence_no_match(self):
        from src.services.surface_detail_builders import build_recommendation_evidence

        surface = _mock_surface(payload={"preview": {"title": "Some recommendation"}})
        result = await build_recommendation_evidence(AsyncMock(), surface)
        assert result.tab_id == "evidence"


class TestRegistryComplete:
    def test_tab_builders_has_38_entries(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        assert len(TAB_BUILDERS) == 38

    def test_all_13_kinds_covered(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        kinds = {k for k, _ in TAB_BUILDERS.keys()}
        expected = {
            "plan",
            "summary",
            "briefing",
            "approval",
            "recommendation",
            "alert",
            "checklist",
            "comparison",
            "timeline",
            "table",
            "activity",
            "proactive_insight",
            "run",
        }
        assert kinds == expected

    def test_all_builders_are_callable(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        for key, builder in TAB_BUILDERS.items():
            assert callable(builder), f"Builder for {key} is not callable"

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
    def test_tab_builders_registry_size(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        # 40 before Step 9 P1; the 5 dead surface kinds (checklist/comparison/
        # timeline/table/activity) removed 10 rows -> 30. The single-lead cutover's
        # prepared-work review queue adds its one ``queue`` tab -> 31.
        assert len(TAB_BUILDERS) == 31

    def test_all_kinds_covered(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        kinds = {k for k, _ in TAB_BUILDERS.keys()}
        expected = {
            "plan",
            "summary",
            "briefing",
            "approval",
            "recommendation",
            "alert",
            "proactive_insight",
            "run",
            "prepared_work",
        }
        assert kinds == expected

    def test_all_builders_are_callable(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        for key, builder in TAB_BUILDERS.items():
            assert callable(builder), f"Builder for {key} is not callable"

    def test_registry_exact_snapshot(self):
        """Pin the exact (kind, tab_id) -> builder-name mapping.

        Characterization snapshot for the package split (SVC-P2-2a): the
        facade-assembled TAB_BUILDERS must map the identical keys to the
        identically-named async builders, regardless of which submodule each
        now lives in.
        """
        import inspect

        from src.services.surface_detail_builders import TAB_BUILDERS

        expected = {
            ("run", "steps"): "build_run_steps_tab",
            ("run", "plan"): "build_run_plan_tab",
            ("run", "events"): "build_run_events_tab",
            ("run", "trace"): "build_run_trace_tab",
            ("run", "approval"): "build_run_approval_tab",
            ("summary", "steps"): "build_run_steps_tab",
            ("summary", "plan"): "build_run_plan_tab",
            ("summary", "events"): "build_run_events_tab",
            ("summary", "trace"): "build_run_trace_tab",
            ("summary", "approval"): "build_run_approval_tab",
            ("plan", "overview"): "build_plan_overview",
            ("plan", "context"): "build_plan_context",
            ("plan", "execution"): "build_plan_execution",
            ("summary", "overview"): "build_summary_overview",
            ("summary", "sources"): "build_summary_sources",
            ("summary", "context"): "build_summary_context",
            ("briefing", "priorities"): "build_briefing_priorities",
            ("briefing", "events"): "build_briefing_events",
            ("briefing", "actions"): "build_briefing_actions",
            ("approval", "request"): "build_approval_request",
            ("approval", "risk"): "build_approval_risk",
            ("approval", "history"): "build_approval_history",
            ("recommendation", "overview"): "build_recommendation_overview",
            ("recommendation", "evidence"): "build_recommendation_evidence",
            ("recommendation", "context"): "build_recommendation_context",
            ("alert", "overview"): "build_alert_overview",
            ("alert", "diagnostics"): "build_alert_diagnostics",
            ("proactive_insight", "signal"): "build_insight_signal",
            ("proactive_insight", "actions"): "build_insight_actions",
            ("proactive_insight", "context"): "build_insight_context",
            ("prepared_work", "queue"): "build_prepared_work_queue",
        }
        actual = {key: builder.__name__ for key, builder in TAB_BUILDERS.items()}
        assert actual == expected
        for key, builder in TAB_BUILDERS.items():
            assert inspect.iscoroutinefunction(builder), f"{key} builder is not async"

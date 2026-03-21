"""Tests for connector insight service."""

from unittest.mock import AsyncMock

from tests.conftest import TEST_WORKSPACE_ID


class TestConnectorInsight:
    async def test_get_dependencies_gmail(self):
        from src.services.connector_insight import ConnectorInsightService

        db = AsyncMock()
        svc = ConnectorInsightService(db, TEST_WORKSPACE_ID)
        deps = svc.get_dependencies("gmail")
        assert len(deps) > 0
        providers = {d.target_provider for d in deps}
        assert "calendar" in providers

    async def test_get_dependencies_github(self):
        from src.services.connector_insight import ConnectorInsightService

        db = AsyncMock()
        svc = ConnectorInsightService(db, TEST_WORKSPACE_ID)
        deps = svc.get_dependencies("github")
        assert len(deps) > 0
        providers = {d.target_provider for d in deps}
        assert "slack" in providers

    async def test_get_dependencies_unknown_provider(self):
        from src.services.connector_insight import ConnectorInsightService

        db = AsyncMock()
        svc = ConnectorInsightService(db, TEST_WORKSPACE_ID)
        deps = svc.get_dependencies("unknown_provider")
        assert deps == []

    async def test_recommendations_inactive_provider(self):
        from src.services.connector_insight import (
            ConnectorInsightService,
            DownstreamImpact,
            SyncHealthReport,
        )

        db = AsyncMock()
        svc = ConnectorInsightService(db, TEST_WORKSPACE_ID)

        health = SyncHealthReport(
            provider="gmail",
            status="inactive",
            events_last_24h=0,
            events_last_7d=10,
            last_event_at=None,
            avg_events_per_day=1.4,
            error_rate=0.0,
            latency_trend="stable",
        )
        impact = DownstreamImpact(
            entities_created=5,
            memories_influenced=3,
            plans_triggered=0,
            briefings_contributed=0,
            active_webhook_count=0,
        )

        recs = svc._build_recommendations(health, impact)
        assert len(recs) > 0
        assert any("credential" in r.lower() or "re-authenticate" in r.lower() for r in recs)

    async def test_recommendations_no_webhooks(self):
        from src.services.connector_insight import (
            ConnectorInsightService,
            DownstreamImpact,
            SyncHealthReport,
        )

        db = AsyncMock()
        svc = ConnectorInsightService(db, TEST_WORKSPACE_ID)

        health = SyncHealthReport(
            provider="github",
            status="healthy",
            events_last_24h=10,
            events_last_7d=70,
            last_event_at=None,
            avg_events_per_day=10.0,
            error_rate=0.0,
            latency_trend="stable",
        )
        impact = DownstreamImpact(
            entities_created=5,
            memories_influenced=3,
            plans_triggered=0,
            briefings_contributed=0,
            active_webhook_count=0,
        )

        recs = svc._build_recommendations(health, impact)
        assert any("push" in r.lower() for r in recs)

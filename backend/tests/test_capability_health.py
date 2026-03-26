"""Tests for capability health service."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_WORKSPACE_ID


class TestCapabilityHealth:
    async def test_internal_always_healthy(self):
        from src.services.capability_health import CapabilityHealthService

        db = AsyncMock()
        # Mock installation check returning 0 (unconfigured)
        db.scalar = AsyncMock(return_value=0)

        # Mock event query returning no results
        mock_result = MagicMock()
        mock_result.first.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        svc = CapabilityHealthService(db, TEST_WORKSPACE_ID)
        report = await svc.get_health_report()

        internal = next(f for f in report.families if f.family == "internal")
        assert internal.status == "healthy"
        assert internal.provider == "jarvis"

    async def test_count_capabilities(self):
        from src.integrations.capabilities import CapabilityFamily
        from src.services.capability_health import CapabilityHealthService

        db = AsyncMock()
        svc = CapabilityHealthService(db, TEST_WORKSPACE_ID)

        email_count = svc._count_capabilities(CapabilityFamily.EMAIL)
        assert email_count > 0

        internal_count = svc._count_capabilities(CapabilityFamily.INTERNAL)
        assert internal_count > 0

    async def test_family_providers_mapping(self):
        from src.services.capability_health import FAMILY_PROVIDERS

        assert "gmail" in FAMILY_PROVIDERS["email"]
        assert "github" in FAMILY_PROVIDERS["repo"]
        assert "slack" in FAMILY_PROVIDERS["messaging"]
        assert FAMILY_PROVIDERS["internal"] == []

    async def test_get_family_status_invalid(self):
        from src.services.capability_health import CapabilityHealthService

        db = AsyncMock()
        svc = CapabilityHealthService(db, TEST_WORKSPACE_ID)
        result = await svc.get_family_status("nonexistent_family")
        assert result is None

    async def test_report_counts(self):
        from src.services.capability_health import CapabilityHealthService

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        mock_result = MagicMock()
        mock_result.first.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        svc = CapabilityHealthService(db, TEST_WORKSPACE_ID)
        report = await svc.get_health_report()

        total = (
            report.healthy_count
            + report.degraded_count
            + report.unavailable_count
            + report.unconfigured_count
        )
        assert total == len(report.families)

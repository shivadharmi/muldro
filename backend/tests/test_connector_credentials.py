"""Tests for connector credential lifecycle — dynamic sources."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.poll_result import PollResult
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_raw_event


class TestGetCredentialsUsesOAuthManager:
    async def test_returns_plaintext_token(self):
        from src.services.integration_manager import IntegrationManager

        mock_db = AsyncMock()
        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token = AsyncMock(return_value="plaintext_token_123")

        mgr = IntegrationManager(mock_db, oauth_manager=mock_oauth)
        creds = await mgr._get_credentials("usr_1", "gmail")

        assert creds == {"access_token": "plaintext_token_123"}
        mock_oauth.get_valid_token.assert_called_once_with("usr_1", "google")


class TestDynamicObservationSources:
    async def test_default_sources(self):
        from src.services.settings_service import SettingsService

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        svc = SettingsService(mock_db)
        sources = await svc.get_observation_sources("usr_1")

        providers = [s["provider"] for s in sources]
        assert "gmail" in providers
        assert "calendar" in providers
        assert "github" in providers
        assert "slack" in providers
        assert all(s["enabled"] for s in sources)

    async def test_get_observation_intervals_uses_dynamic_sources(self):
        from src.services.settings_service import SettingsService

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        svc = SettingsService(mock_db)
        intervals = await svc.get_observation_intervals("usr_1")

        assert intervals["gmail"] == 30
        assert intervals["calendar"] == 180
        assert intervals["github"] == 60
        assert intervals["slack"] == 15


class TestPollIntegrationConsumesPollResult:
    """Regression tests: poll_integration must consume PollResult, not unpack a 2-tuple."""

    def _make_installation(self, provider: str = "gmail"):
        inst = MagicMock()
        inst.install_id = "inst_001"
        inst.user_id = TEST_USER_ID
        inst.workspace_id = TEST_WORKSPACE_ID
        inst.server_name = provider
        inst.status = "active"
        inst.health_status = "unknown"
        return inst

    def _make_db(self, installation):
        mock_db = AsyncMock()
        # scalar_one_or_none returns installation on first call, None cursor on second
        result_inst = MagicMock()
        result_inst.scalar_one_or_none = MagicMock(return_value=installation)
        result_cursor = MagicMock()
        result_cursor.first = MagicMock(return_value=None)
        result_cursor.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(side_effect=[result_inst, result_cursor])
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        return mock_db

    @pytest.mark.asyncio
    async def test_poll_integration_success_poll_result_ingests_events_and_advances_cursor(self):
        """Successful PollResult: events published + cursor advanced."""
        from src.services.integration_manager import IntegrationManager

        installation = self._make_installation("gmail")
        mock_db = self._make_db(installation)

        event = make_raw_event(source="gmail")
        success_result = PollResult(events=[event], cursor="tok_next", error_class="none")

        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token = AsyncMock(return_value="tok_123")

        mock_event_bus = AsyncMock()
        mock_event_bus.event_stream = MagicMock(return_value="stream:ws_test")

        mock_connector_instance = AsyncMock()
        mock_connector_instance.poll = AsyncMock(return_value=success_result)

        mock_connector_cls = MagicMock(return_value=mock_connector_instance)

        with patch(
            "src.services.integration_manager.CONNECTOR_REGISTRY",
            {"gmail": mock_connector_cls},
        ):
            mgr = IntegrationManager(
                mock_db,
                event_bus=mock_event_bus,
                oauth_manager=mock_oauth,
            )
            # Patch _update_cursor to avoid a real DB upsert
            mgr._update_cursor = AsyncMock()
            mgr._get_cursor = AsyncMock(return_value=None)
            result = await mgr.poll_integration("inst_001", TEST_USER_ID)

        assert result["events"] == 1
        assert result["provider"] == "gmail"
        assert result["new_cursor"] == "tok_next"
        # Cursor MUST be advanced on success
        mgr._update_cursor.assert_awaited_once_with(
            TEST_USER_ID, "gmail", "tok_next", TEST_WORKSPACE_ID
        )
        # Event published to bus
        mock_event_bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_integration_failed_poll_result_does_not_advance_cursor(self):
        """Failed PollResult: cursor must NOT be advanced; no events ingested."""
        from src.services.integration_manager import IntegrationManager

        installation = self._make_installation("gmail")
        mock_db = self._make_db(installation)

        failed_result = PollResult(events=[], cursor=None, error_class="transient")

        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token = AsyncMock(return_value="tok_123")

        mock_connector_instance = AsyncMock()
        mock_connector_instance.poll = AsyncMock(return_value=failed_result)
        mock_connector_cls = MagicMock(return_value=mock_connector_instance)

        with patch(
            "src.services.integration_manager.CONNECTOR_REGISTRY",
            {"gmail": mock_connector_cls},
        ):
            mgr = IntegrationManager(mock_db, oauth_manager=mock_oauth)
            mgr._update_cursor = AsyncMock()
            mgr._get_cursor = AsyncMock(return_value="old_cursor")
            result = await mgr.poll_integration("inst_001", TEST_USER_ID)

        assert result["events"] == 0
        assert result["error"] == "transient"
        # Cursor MUST NOT be advanced on failure
        mgr._update_cursor.assert_not_awaited()

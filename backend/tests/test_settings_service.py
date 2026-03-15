"""Tests for SettingsService — per-user configuration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.settings_service import SettingsService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def service(mock_db):
    return SettingsService(mock_db)


class TestGetAll:
    async def test_returns_defaults_when_empty(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        settings = await service.get_all("usr_default")

        assert "policy" in settings
        assert settings["policy"]["mode"] == "approval_required"
        assert settings["budget"]["daily_limit_usd"] == 5.0

    async def test_user_overrides_take_precedence(self, service, mock_db):
        row = MagicMock()
        row.category = "policy"
        row.key = "mode"
        row.value = "full_auto"

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        mock_db.execute = AsyncMock(return_value=result_mock)

        settings = await service.get_all("usr_default")
        assert settings["policy"]["mode"] == "full_auto"


class TestGet:
    async def test_returns_stored_value(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "lockdown"
        mock_db.execute = AsyncMock(return_value=result_mock)

        val = await service.get("usr_default", "policy", "mode")
        assert val == "lockdown"

    async def test_returns_default_when_not_set(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        val = await service.get("usr_default", "policy", "mode")
        assert val == "approval_required"


class TestSet:
    async def test_creates_new_setting(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        await service.set("usr_default", "policy", "mode", "full_auto")
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    async def test_updates_existing_setting(self, service, mock_db):
        existing = MagicMock()
        existing.value = "approval_required"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)

        await service.set("usr_default", "policy", "mode", "lockdown")
        assert existing.value == "lockdown"
        mock_db.flush.assert_called_once()


class TestPolicyMode:
    async def test_returns_mode(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "suggest_only"
        mock_db.execute = AsyncMock(return_value=result_mock)

        mode = await service.get_policy_mode("usr_default")
        assert mode == "suggest_only"


class TestBudgetLimit:
    async def test_returns_limit(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = 10.0
        mock_db.execute = AsyncMock(return_value=result_mock)

        limit = await service.get_budget_limit("usr_default")
        assert limit == 10.0

    async def test_returns_default(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        limit = await service.get_budget_limit("usr_default")
        assert limit == 5.0


class TestObservationIntervals:
    async def test_returns_intervals(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        intervals = await service.get_observation_intervals("usr_default")
        assert "gmail" in intervals
        assert intervals["gmail"] == 30
        assert intervals["slack"] == 15

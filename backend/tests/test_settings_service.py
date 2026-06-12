"""Tests for SettingsService — per-user configuration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.settings_service import SettingsService
from tests.conftest import TEST_USER_ID


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

        settings = await service.get_all(TEST_USER_ID)

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

        settings = await service.get_all(TEST_USER_ID)
        assert settings["policy"]["mode"] == "full_auto"


class TestGet:
    async def test_returns_stored_value(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "lockdown"
        mock_db.execute = AsyncMock(return_value=result_mock)

        val = await service.get(TEST_USER_ID, "policy", "mode")
        assert val == "lockdown"

    async def test_returns_default_when_not_set(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        val = await service.get(TEST_USER_ID, "policy", "mode")
        assert val == "approval_required"


class TestSet:
    """``SettingsService.set`` is branch-free — a single atomic upsert
    handles both create and update. Tests verify the statement carries the
    correct key columns and value."""

    async def test_upsert_carries_params_and_flushes(self, service, mock_db):
        captured: dict = {}
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))

        await service.set(TEST_USER_ID, "policy", "mode", "full_auto")

        assert mock_db.execute.await_count == 1
        params = captured["stmt"].compile().params
        assert params["user_id"] == TEST_USER_ID
        assert params["category"] == "policy"
        assert params["key"] == "mode"
        assert params["value"] == "full_auto"
        mock_db.flush.assert_awaited_once()

    async def test_upsert_with_dict_value(self, service, mock_db):
        captured: dict = {}
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))

        await service.set(TEST_USER_ID, "notification", "digest", {"frequency": "weekly"})

        params = captured["stmt"].compile().params
        assert params["value"] == {"frequency": "weekly"}


class TestPolicyMode:
    async def test_returns_mode(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "suggest_only"
        mock_db.execute = AsyncMock(return_value=result_mock)

        mode = await service.get_policy_mode(TEST_USER_ID)
        assert mode == "suggest_only"


class TestBudgetLimit:
    async def test_returns_limit(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = 10.0
        mock_db.execute = AsyncMock(return_value=result_mock)

        limit = await service.get_budget_limit(TEST_USER_ID)
        assert limit == 10.0

    async def test_returns_default(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        limit = await service.get_budget_limit(TEST_USER_ID)
        assert limit == 5.0


class TestObservationIntervals:
    async def test_returns_intervals(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        intervals = await service.get_observation_intervals(TEST_USER_ID)
        assert "gmail" in intervals
        assert intervals["gmail"] == 30
        assert intervals["slack"] == 15

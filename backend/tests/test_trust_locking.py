"""Tests for pessimistic locking in trust state queries."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.services.risk_assessor import get_or_create_trust_state


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestTrustStateLocking:
    async def test_select_contains_for_update(self, mock_db):
        """get_or_create_trust_state must issue SELECT ... FOR UPDATE."""
        captured_stmts: list = []

        async def capturing_execute(stmt, *args, **kwargs):
            captured_stmts.append(stmt)
            result = MagicMock()
            result.scalar_one_or_none.return_value = MagicMock()  # existing state
            return result

        mock_db.execute = AsyncMock(side_effect=capturing_execute)

        await get_or_create_trust_state(mock_db, "ws_test", "email.send", "low")

        assert len(captured_stmts) == 1, "Expected exactly one SELECT statement"
        compiled = str(captured_stmts[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in compiled, f"Expected FOR UPDATE in SQL, got: {compiled}"

    async def test_existing_state_returned_without_insert(self, mock_db):
        """When a TrustState already exists, it is returned without creating a new one."""
        existing = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)

        state = await get_or_create_trust_state(mock_db, "ws_test", "email.send", "low")

        assert state is existing
        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_awaited()

    async def test_new_state_created_when_missing(self, mock_db):
        """When no TrustState exists, a new one is created and flushed."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        state = await get_or_create_trust_state(mock_db, "ws_test", "email.send", "low")

        assert state.workspace_id == "ws_test"
        assert state.capability == "email.send"
        assert state.risk_level == "low"
        assert state.trust_level == "first_use"
        assert state.approved_count == 0
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

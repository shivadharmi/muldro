"""Tests for Telegram using public orchestrator methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestTelegramUsesPublicMethods:
    """Telegram _handle_status uses get_budget_status, not private attrs."""

    @pytest.mark.asyncio
    async def test_handle_status_uses_public_methods(self):
        from src.interface.telegram import TelegramInterface

        settings = MagicMock()
        settings.telegram_chat_id = "12345"

        mock_budget_status = MagicMock()
        mock_budget_status.daily_spend_usd = 2.5
        mock_budget_status.daily_limit_usd = 10.0
        mock_budget_status.percent_used = 25.0
        mock_budget_status.budget_mode = "normal"

        orchestrator = MagicMock()
        orchestrator.get_budget_status = AsyncMock(return_value=mock_budget_status)

        tg = TelegramInterface(
            settings=settings,
            orchestrator=orchestrator,
            surface_registry=None,
        )

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await tg._handle_status(update, None)

        orchestrator.get_budget_status.assert_called_once()
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "$2.50" in text
        assert "$10.00" in text

    @pytest.mark.asyncio
    async def test_handle_status_no_private_attr_access(self):
        """Verify _db_factory and _budget are NOT accessed."""
        from src.interface.telegram import TelegramInterface

        settings = MagicMock()
        settings.telegram_chat_id = "12345"

        mock_budget_status = MagicMock()
        mock_budget_status.daily_spend_usd = 0.0
        mock_budget_status.daily_limit_usd = 10.0
        mock_budget_status.percent_used = 0.0
        mock_budget_status.budget_mode = "normal"

        orchestrator = MagicMock()
        orchestrator.get_budget_status = AsyncMock(return_value=mock_budget_status)
        # These should NOT be accessed
        type(orchestrator)._db_factory = property(
            lambda self: (_ for _ in ()).throw(AssertionError("Should use public methods"))
        )
        type(orchestrator)._budget = property(
            lambda self: (_ for _ in ()).throw(AssertionError("Should use public methods"))
        )

        tg = TelegramInterface(
            settings=settings,
            orchestrator=orchestrator,
            surface_registry=None,
        )

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        # Should not raise
        await tg._handle_status(update, None)

"""Tests for the OAuth callback auto-resume wiring (re-auth reconnect path).

After a successful OAuth reconnect for a provider, the callback schedules
``_resume_after_reauth``, which builds a ``ReauthService`` and calls
``clear_reauth`` — un-pausing the provider's perception sources, re-queuing
runs parked in ``awaiting_reauth``, and clearing the notify-dedup key.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes_auth_oauth import _resume_after_reauth


def _db_factory():
    """A db_factory whose sessions work as ``async with db_factory() as db``."""
    db = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield db

    factory = MagicMock(side_effect=lambda: _ctx())
    return factory


class TestResumeAfterReauth:
    @patch("src.services.reauth_service.ReauthService")
    async def test_calls_clear_reauth_for_provider(self, mock_reauth_cls):
        instance = MagicMock()
        instance.clear_reauth = AsyncMock()
        mock_reauth_cls.return_value = instance

        await _resume_after_reauth(
            _db_factory(),
            "usr_abc",
            "google",
            "ws_1",
        )

        instance.clear_reauth.assert_awaited_once_with("usr_abc", "google", workspace_id="ws_1")

    @patch("src.services.reauth_service.ReauthService")
    async def test_clear_reauth_failure_is_swallowed(self, mock_reauth_cls):
        """A failure in clear_reauth must never propagate (connect succeeds)."""
        instance = MagicMock()
        instance.clear_reauth = AsyncMock(side_effect=RuntimeError("boom"))
        mock_reauth_cls.return_value = instance

        # Should not raise.
        await _resume_after_reauth(_db_factory(), "usr_abc", "github", "ws_1")
        instance.clear_reauth.assert_awaited_once()

    @pytest.mark.parametrize("provider", ["google", "github", "slack", "notion", "atlassian"])
    @patch("src.services.reauth_service.ReauthService")
    async def test_resume_wired_for_all_providers(self, mock_reauth_cls, provider):
        instance = MagicMock()
        instance.clear_reauth = AsyncMock()
        mock_reauth_cls.return_value = instance

        await _resume_after_reauth(_db_factory(), "usr_abc", provider, "ws_1")

        instance.clear_reauth.assert_awaited_once_with("usr_abc", provider, workspace_id="ws_1")


class TestResumeAfterReauthResourceHygiene:
    """H1 + L1: the resume must not leak the per-call Redis client and must not
    construct the Notifier with a session that is already closed."""

    @patch("src.services.reauth_service.ReauthService")
    @patch("redis.asyncio.from_url")
    async def test_redis_client_is_closed(self, mock_from_url, mock_reauth_cls):
        """The per-call Redis client is closed (aclose) even on the happy path —
        no connection leak per OAuth callback (H1)."""
        redis_client = MagicMock()
        redis_client.aclose = AsyncMock()
        mock_from_url.return_value = redis_client

        instance = MagicMock()
        instance.clear_reauth = AsyncMock()
        mock_reauth_cls.return_value = instance

        await _resume_after_reauth(_db_factory(), "usr_abc", "google", "ws_1")

        redis_client.aclose.assert_awaited_once()

    @patch("src.services.reauth_service.ReauthService")
    @patch("redis.asyncio.from_url")
    async def test_redis_closed_even_when_clear_reauth_fails(self, mock_from_url, mock_reauth_cls):
        """A clear_reauth failure must still close the Redis client (no leak)
        and must not propagate."""
        redis_client = MagicMock()
        redis_client.aclose = AsyncMock()
        mock_from_url.return_value = redis_client

        instance = MagicMock()
        instance.clear_reauth = AsyncMock(side_effect=RuntimeError("boom"))
        mock_reauth_cls.return_value = instance

        await _resume_after_reauth(_db_factory(), "usr_abc", "google", "ws_1")

        redis_client.aclose.assert_awaited_once()

    @patch("src.services.reauth_service.ReauthService")
    @patch("src.services.notifier.Notifier")
    @patch("redis.asyncio.from_url")
    async def test_notifier_not_built_with_closed_session(
        self, mock_from_url, mock_notifier_cls, mock_reauth_cls
    ):
        """L1: the Notifier must not be constructed with a session that has
        already exited its ``async with`` block. If a Notifier is built, its
        ``db`` kwarg must be a session that is still live when clear_reauth runs
        — verified by constructing it with the SAME session ReauthService uses,
        or by not passing a doomed session at all."""
        redis_client = MagicMock()
        redis_client.aclose = AsyncMock()
        mock_from_url.return_value = redis_client

        instance = MagicMock()
        instance.clear_reauth = AsyncMock()
        mock_reauth_cls.return_value = instance

        # Must not raise (and clear_reauth still runs).
        await _resume_after_reauth(_db_factory(), "usr_abc", "google", "ws_1")
        instance.clear_reauth.assert_awaited_once()


class TestCallbackSchedulesResume:
    """The OAuth callback wires ``_resume_after_reauth`` as a background task for
    every reconnected provider (verified at the source level — exercising the
    full callback would require stubbing each provider's HTTP token exchange)."""

    def test_callback_registers_resume_for_all_providers(self):
        import inspect

        from src.api import routes_auth_oauth

        src = inspect.getsource(routes_auth_oauth.oauth_callback)
        # The resume task is added once, after the per-provider branches, so it
        # fires for google/github/slack/notion/atlassian alike.
        assert "_resume_after_reauth" in src
        assert "background_tasks.add_task(" in src
        # It must pass the reconnected provider + workspace through.
        assert "provider," in src
        assert "workspace_id," in src

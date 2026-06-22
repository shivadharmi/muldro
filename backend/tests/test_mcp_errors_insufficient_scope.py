"""Regression: an OAuth *insufficient-scope* 403 must be treated as a permanent
re-authorization need (``auth_required``), NOT a transient runtime ``auth_error``.

Real-world trigger (see log2.log): ``manage_gmail_filter`` returns

    HttpError 403 ... "Request had insufficient authentication scopes."
    ... 'reason': 'insufficientPermissions'

The connected Google token can search/label mail but was never consented for
``gmail.settings.basic``. Re-fetching the bearer cannot fix a narrow *grant*;
only a user re-consent can. So this 403 must light up the pre-built re-auth
pipeline (agent-loop short-circuit + dag_runner defer + notify), which is gated
solely on ``error_code == "auth_required"`` carrying ``provider``/``server``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.mcp_errors import (
    MCPErrorCode,
    classify_error,
    is_insufficient_scope,
)

# The exact upstream message shape from the Google Workspace MCP.
INSUFFICIENT_SCOPE_MSG = (
    "Error calling tool 'manage_gmail_filter': API error in manage_gmail_filter: "
    "<HttpError 403 when requesting "
    "https://gmail.googleapis.com/gmail/v1/users/me/settings/filters?alt=json "
    'returned "Request had insufficient authentication scopes.". '
    "Details: \"[{'message': 'Insufficient Permission', 'domain': 'global', "
    "'reason': 'insufficientPermissions'}]\">"
)


class TestIsInsufficientScope:
    def test_matches_real_gmail_settings_403(self):
        assert is_insufficient_scope(Exception(INSUFFICIENT_SCOPE_MSG)) is True

    def test_matches_reason_insufficient_permissions(self):
        assert is_insufficient_scope("reason: insufficientPermissions") is True

    def test_generic_401_is_not_insufficient_scope(self):
        # A stale-token mid-session 401 is transiently refreshable — NOT a
        # grant-scope problem. Must stay out of the re-auth path.
        assert is_insufficient_scope(Exception("401 Unauthorized: token expired")) is False

    def test_generic_403_forbidden_is_not_insufficient_scope(self):
        assert is_insufficient_scope(Exception("403 Forbidden: rate limited")) is False


class TestClassifyErrorUnchanged:
    """``classify_error`` keeps mapping generic 401/403 to AUTH_ERROR — the
    insufficient-scope routing is a *narrow* addition, not a broadening of
    every auth error into a re-auth prompt."""

    def test_generic_403_still_auth_error(self):
        assert classify_error(Exception("403 Forbidden")) == MCPErrorCode.AUTH_ERROR


class TestSessionPoolEmitsAuthRequiredOnScope403:
    @pytest.mark.asyncio
    async def test_insufficient_scope_returns_auth_required_envelope(self):
        from src.integrations.session_pool import UserMCPSessionPool

        pool = UserMCPSessionPool()

        fake_session = MagicMock()
        fake_session.client.call_tool = AsyncMock(side_effect=Exception(INSUFFICIENT_SCOPE_MSG))

        with (
            patch.object(pool, "get_or_create_session", AsyncMock(return_value=fake_session)),
            patch.object(pool._circuit_breaker, "is_available", return_value=True),
            patch.object(pool, "refresh_session", AsyncMock()) as refresh,
        ):
            result = await pool.call_tool(
                "manage_gmail_filter",
                {"action": "create"},
                user_id="usr_1",
                server_name="google-workspace",
                workspace_id="ws_1",
            )

        # The whole re-auth pipeline keys on these three fields.
        assert result["status"] == "error"
        assert result["error_code"] == MCPErrorCode.AUTH_REQUIRED
        assert result["provider"] == "google"
        assert result["server"] == "google-workspace"
        # A scope grant cannot be fixed by re-fetching the bearer, so we must
        # not waste a token refresh on it.
        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generic_tool_error_still_returns_plain_error(self):
        """A non-scope failure must NOT be misrouted into the re-auth path."""
        from src.integrations.session_pool import UserMCPSessionPool

        pool = UserMCPSessionPool()
        fake_session = MagicMock()
        fake_session.client.call_tool = AsyncMock(side_effect=Exception("boom: something broke"))

        with (
            patch.object(pool, "get_or_create_session", AsyncMock(return_value=fake_session)),
            patch.object(pool._circuit_breaker, "is_available", return_value=True),
            patch.object(pool, "refresh_session", AsyncMock()),
            patch.object(pool, "_is_oauth_server", return_value=False),
        ):
            result = await pool.call_tool(
                "search_gmail_messages",
                {"query": "x"},
                user_id="usr_1",
                server_name="google-workspace",
                workspace_id="ws_1",
            )

        assert result["status"] == "error"
        assert result["error_code"] != MCPErrorCode.AUTH_REQUIRED

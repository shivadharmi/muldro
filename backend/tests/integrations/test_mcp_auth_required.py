"""Tests for the McpAuthRequiredError signal and its session_pool wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.mcp_errors import (
    McpAuthRequiredError,
    MCPErrorCode,
    classify_error,
)
from src.integrations.session_pool import UserMCPSessionPool


class TestMcpAuthRequiredError:
    def test_fields_and_message(self):
        err = McpAuthRequiredError(provider="google", server="google-workspace", reason="revoked")
        assert err.provider == "google"
        assert err.server == "google-workspace"
        assert err.reason == "revoked"
        assert "google" in str(err)
        assert "re-authorization" in str(err)
        assert "revoked" in str(err)

    def test_classify_error_returns_auth_required(self):
        err = McpAuthRequiredError(provider="slack", server="slack", reason="no_token")
        assert classify_error(err) == MCPErrorCode.AUTH_REQUIRED

    def test_auth_required_code_distinct_from_auth_error(self):
        assert MCPErrorCode.AUTH_REQUIRED != MCPErrorCode.AUTH_ERROR
        assert MCPErrorCode.AUTH_REQUIRED == "auth_required"


class TestResolveAuthRaisesOnPermanent:
    def _pool_with_oauth(self, token_result):
        pool = UserMCPSessionPool()
        oauth = MagicMock()
        oauth.get_valid_token_with_reason = AsyncMock(return_value=token_result)
        pool._oauth_manager = oauth
        return pool

    @pytest.mark.asyncio
    async def test_revoked_raises_auth_required(self):
        from src.services.oauth_manager import TokenResult

        pool = self._pool_with_oauth(TokenResult(token=None, reason="revoked"))
        with pytest.raises(McpAuthRequiredError) as exc:
            await pool._resolve_auth("github", "u1", {"auth_provider": "github"})
        assert exc.value.provider == "github"
        assert exc.value.reason == "revoked"

    @pytest.mark.asyncio
    async def test_no_token_raises_auth_required(self):
        from src.services.oauth_manager import TokenResult

        pool = self._pool_with_oauth(TokenResult(token=None, reason="no_token"))
        with pytest.raises(McpAuthRequiredError):
            await pool._resolve_auth("slack", "u1", {"auth_provider": "slack"})

    @pytest.mark.asyncio
    async def test_no_refresh_token_raises_auth_required(self):
        from src.services.oauth_manager import TokenResult

        pool = self._pool_with_oauth(TokenResult(token=None, reason="no_refresh_token"))
        with pytest.raises(McpAuthRequiredError):
            await pool._resolve_auth("notion", "u1", {"auth_provider": "notion"})

    @pytest.mark.asyncio
    async def test_refresh_failed_returns_none(self):
        from src.services.oauth_manager import TokenResult

        pool = self._pool_with_oauth(TokenResult(token=None, reason="refresh_failed"))
        result = await pool._resolve_auth("github", "u1", {"auth_provider": "github"})
        assert result is None

    @pytest.mark.asyncio
    async def test_ok_returns_bearer(self):
        from fastmcp.client.auth import BearerAuth

        from src.services.oauth_manager import TokenResult

        pool = self._pool_with_oauth(TokenResult(token="tok-123", reason="ok"))
        result = await pool._resolve_auth("github", "u1", {"auth_provider": "github"})
        assert isinstance(result, BearerAuth)


class TestCallToolCatchesAuthRequired:
    @pytest.mark.asyncio
    async def test_call_tool_returns_structured_auth_required(self):
        pool = UserMCPSessionPool()
        # Force the circuit available, then make session creation raise.
        pool._circuit_breaker.is_available = MagicMock(return_value=True)

        async def _raise(*a, **k):
            raise McpAuthRequiredError(
                provider="google", server="google-workspace", reason="revoked"
            )

        with patch.object(pool, "get_or_create_session", _raise):
            result = await pool.call_tool(
                "gmail_search",
                {},
                user_id="u1",
                server_name="google-workspace",
                workspace_id="ws_1",
            )

        assert result["status"] == "error"
        assert result["error_code"] == "auth_required"
        assert result["provider"] == "google"
        assert result["server"] == "google-workspace"
        assert "error" in result


class TestStdioGuardRaisesAuthRequired:
    @pytest.mark.asyncio
    async def test_slack_stdio_without_token_raises_auth_required(self):
        pool = UserMCPSessionPool()
        pool.register_server_config(
            "slack",
            {
                "transport": "stdio",
                "auth_provider": "slack",
                "command": "npx",
                "args": ["slack-mcp-server"],
            },
            workspace_id="ws_1",
        )
        client_mock = MagicMock()
        with (
            patch.object(pool, "_resolve_auth", AsyncMock(return_value=None)),
            patch("src.integrations.session_pool.Client", client_mock),
            patch.object(pool, "_register_discovered_tools", AsyncMock()),
        ):
            with pytest.raises(McpAuthRequiredError) as exc:
                await pool.get_or_create_session("slack", user_id="u1", workspace_id="ws_1")
        client_mock.assert_not_called()
        assert exc.value.provider == "slack"
        assert exc.value.server == "slack"

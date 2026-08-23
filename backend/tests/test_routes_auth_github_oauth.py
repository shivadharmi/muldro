"""The native GitHub OAuth connect route, and the routing it restores.

GitHub deliberately holds TWO credentials with two jobs. The gateway/platform-JWT
credential backs the ``github.*`` MCP actions and is untouched here. The token
this route mints is read by exactly one thing: ``GitHubConnector`` polling
https://api.github.com/notifications, which no OpenConnector action can replace.

While the source was claimed as gateway-backed, ``connector_poller`` found a
non-``GatewayConnector`` and skipped every poll with a synthetic transient error,
so the perception row accrued failures forever. These tests pin both halves of
the fix: the source routes natively, and the action path did not move.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.api.routes_auth_oauth import oauth_authorize, oauth_callback
from tests.conftest import TEST_USER_ID, make_mock_settings

_REDIRECT_URI = "http://localhost:8000/v1/auth/github/callback"


def _settings(**overrides):
    base = dict(
        github_oauth_client_id="gh_client_id",
        github_oauth_client_secret="gh_client_secret",
        github_oauth_redirect_uri=_REDIRECT_URI,
        frontend_url="http://localhost:3000",
    )
    base.update(overrides)
    return make_mock_settings(**base)


# ---------------------------------------------------------------------------
# Routing: the perception source went native; the action path did not move.
# ---------------------------------------------------------------------------
def test_github_source_is_no_longer_gateway_claimed():
    """``gateway_provider_for_source`` is what selects poll()'s credential branch.

    None means ``connector_poller`` takes the OAuthManager branch and actually
    polls. Any non-None answer sends it back to the gateway branch, where a
    non-GatewayConnector is skipped every tick.
    """
    from src.integrations.gateway_actions import gateway_provider_for_source

    assert gateway_provider_for_source("github") is None


def test_the_other_gateway_sources_are_untouched():
    """Un-claiming github must not un-claim the sources that really are ported."""
    from src.integrations.gateway_actions import gateway_provider_for_source

    assert gateway_provider_for_source("gmail") == "gmail"
    assert gateway_provider_for_source("calendar") == "googlecalendar"


def test_github_actions_still_resolve_through_the_gateway():
    """The ACTION path is a separate credential and must not have moved."""
    from src.integrations.gateway_actions import (
        PROVIDER_REGISTRY,
        provider_of_action,
        providers_for_server,
    )

    assert provider_of_action("github.create_issue") == "github"
    assert provider_of_action("github.list_pull_requests") == "github"
    assert providers_for_server("github") == ("github",)
    provider = PROVIDER_REGISTRY["github"]
    assert provider.server_name == "github"
    assert provider.oauth_credential_key == "github"


def test_provider_map_sees_the_github_source():
    """``ReauthService`` pauses and resumes a provider through this map.

    The lookups are identity-shaped, so they answer the same whether or not the
    entry exists — which is why the entry itself is asserted. Being LISTED is
    what makes a revoked notifications token able to pause the source and a
    reconnect able to resume it; the identity fallback is a default, not a
    statement that anything native backs this source.
    """
    from src.integrations.provider_map import (
        _PROVIDER_SOURCES,
        provider_for_source,
        sources_for_provider,
    )

    assert _PROVIDER_SOURCES.get("github") == ["github"]
    assert provider_for_source("github") == "github"
    assert sources_for_provider("github") == ["github"]


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------
class TestGitHubAuthorize:
    async def test_returns_a_github_authorize_url_with_the_notifications_scope(self):
        resp = await oauth_authorize(
            "github", scopes="", user_id=TEST_USER_ID, settings=_settings()
        )

        assert resp.provider == "github"
        assert resp.url.startswith("https://github.com/login/oauth/authorize?")
        assert "gh_client_id" in resp.url
        # urlencode quotes the separators; assert on the decoded params.
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(resp.url).query)
        assert params["scope"] == ["notifications read:user"]
        assert params["state"] == [TEST_USER_ID]
        assert params["redirect_uri"] == [_REDIRECT_URI]

    async def test_does_not_request_the_repo_scope(self):
        """``repo`` is write access to every repository the founder can reach.

        Notifications from PRIVATE repositories need it, so it is the obvious
        "fix" the first time one goes missing. A perception source must not hold
        that authority — if this ever has to change, it is a decision, not a
        patch.
        """
        from urllib.parse import parse_qs, urlparse

        resp = await oauth_authorize(
            "github", scopes="", user_id=TEST_USER_ID, settings=_settings()
        )

        scope = parse_qs(urlparse(resp.url).query)["scope"][0]
        assert "repo" not in scope.split()

    async def test_caller_supplied_scopes_override_the_default(self):
        from urllib.parse import parse_qs, urlparse

        resp = await oauth_authorize(
            "github", scopes="notifications", user_id=TEST_USER_ID, settings=_settings()
        )

        assert parse_qs(urlparse(resp.url).query)["scope"] == ["notifications"]

    async def test_unconfigured_client_id_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            await oauth_authorize(
                "github",
                scopes="",
                user_id=TEST_USER_ID,
                settings=_settings(github_oauth_client_id=""),
            )

        assert exc.value.status_code == 400
        assert "GitHub" in exc.value.detail


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------
def _token_response(payload: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload)
    resp.text = str(payload)
    return resp


@asynccontextmanager
async def _noop_db():
    yield AsyncMock()


def _callback_patches(post: AsyncMock, oauth_mgr: MagicMock):
    """Patch every collaborator the github callback branch reaches."""
    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return (
        patch("httpx.AsyncClient", MagicMock(return_value=client)),
        patch("src.models.database.get_session_factory", MagicMock(return_value=_noop_db)),
        patch("src.api.deps.resolve_workspace_id", AsyncMock(return_value="ws_1")),
        patch("src.services.oauth_manager.OAuthManager", MagicMock(return_value=oauth_mgr)),
        patch("src.api.routes_auth_oauth._ensure_integration", AsyncMock()),
    )


async def _run_callback(payload: dict, status_code: int = 200):
    post = AsyncMock(return_value=_token_response(payload, status_code))
    oauth_mgr = MagicMock()
    oauth_mgr.store_token = AsyncMock()

    patches = _callback_patches(post, oauth_mgr)
    for p in patches:
        p.start()
    try:
        result = await oauth_callback(
            "github",
            BackgroundTasks(),
            code="the_code",
            state=TEST_USER_ID,
            error="",
            settings=_settings(),
        )
    finally:
        for p in reversed(patches):
            p.stop()
    return result, post, oauth_mgr


class TestGitHubCallback:
    async def test_token_exchange_asks_for_json(self):
        """Without ``Accept: application/json`` GitHub answers form-encoded.

        ``resp.json()`` then raises on a response that succeeded — the single
        most common way this integration breaks.
        """
        _result, post, _mgr = await _run_callback({"access_token": "gho_abc"})

        assert post.await_args.args[0] == "https://github.com/login/oauth/access_token"
        assert post.await_args.kwargs["headers"]["Accept"] == "application/json"

    async def test_stores_a_github_token_with_no_refresh_token(self):
        """An OAuth App token does not expire and carries no refresh token."""
        _result, _post, mgr = await _run_callback(
            {"access_token": "gho_abc", "scope": "notifications,read:user"}
        )

        kwargs = mgr.store_token.await_args.kwargs
        assert kwargs["provider"] == "github"
        assert kwargs["access_token"] == "gho_abc"
        assert kwargs["refresh_token"] is None
        assert kwargs["expires_at"] is None
        assert kwargs["scopes"] == ["notifications", "read:user"]

    async def test_an_error_body_returned_with_http_200_is_a_failure(self):
        """GitHub reports a rejected exchange as 200 with an ``error`` key.

        Trusting the status alone stores a missing token as a live connection.
        The reported reason must be GitHub's own — falling through to the generic
        "no access token" message would hide ``bad_verification_code`` (a stale
        or replayed code) behind something that reads like a GitHub outage.
        """
        result, _post, mgr = await _run_callback(
            {"error": "bad_verification_code", "error_description": "The code is incorrect."}
        )

        mgr.store_token.assert_not_awaited()
        assert result.status_code == 307
        location = result.headers["location"]
        assert "error=" in location
        assert "bad_verification_code" in location

    async def test_a_response_with_no_token_is_a_failure(self):
        result, _post, mgr = await _run_callback({"scope": "notifications"})

        mgr.store_token.assert_not_awaited()
        assert "error=" in result.headers["location"]

    async def test_a_non_200_exchange_is_a_failure(self):
        result, _post, mgr = await _run_callback({"error": "server_error"}, status_code=500)

        mgr.store_token.assert_not_awaited()
        assert "error=" in result.headers["location"]

"""Tests for OAuth authentication routes."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id
from tests.conftest import TEST_USER_ID


class TestOAuthConnectRoutes:
    """The native OAuth connect routes, and what they no longer serve.

    ``google`` and ``notion`` moved behind the OpenConnector gateway, which owns
    their OAuth client. Their native authorize/callback branches were deleted, so
    they fall through to the shared "Unknown provider" 400 — even when
    Muldro-side client credentials happen to be configured, which for notion they
    still ARE, because the startup registrar needs them. Minting a token nothing
    reads was the failure mode this closes.

    ``github`` is the exception that proves the rule: its native route came back
    precisely because something DOES read that token — ``GitHubConnector``
    polling /notifications, which no gateway action can replace.
    """

    def _client(self):
        _user = MagicMock()
        _user.user_id = TEST_USER_ID
        app.dependency_overrides[get_current_user] = lambda: _user
        app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
        return TestClient(app, raise_server_exceptions=False)

    def _cleanup(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_id, None)

    def test_google_authorize_is_retired(self):
        """Configured Google credentials must NOT resurrect the native flow."""
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            google_oauth_client_id="test_client_id",
            google_oauth_client_secret="test_secret",
            google_oauth_redirect_uri="http://localhost:3000/auth/callback",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        client = self._client()
        try:
            resp = client.get("/v1/auth/oauth/google/authorize")
            assert resp.status_code == 400
            assert "test_client_id" not in resp.text
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_github_authorize_serves_the_notifications_token(self):
        """github is NOT retired here — the poll needs a token only this mints."""
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            github_oauth_client_id="gh_client_id",
            github_oauth_client_secret="gh_secret",
            github_oauth_redirect_uri="http://localhost:8000/v1/auth/github/callback",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        client = self._client()
        try:
            resp = client.get("/v1/auth/oauth/github/authorize")
            assert resp.status_code == 200
            assert "gh_client_id" in resp.json()["url"]
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_google_callback_is_retired(self):
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            google_oauth_client_id="test_client_id",
            google_oauth_client_secret="test_secret",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        try:
            client = self._client()
            resp = client.get(
                f"/v1/auth/oauth/google/callback?code=test_code&state={TEST_USER_ID}",
                follow_redirects=False,
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_notion_native_authorize_is_retired(self):
        """Notion is gateway-served, so a native authorize URL would mint a dead token.

        Its OAuth client settings are still populated — the startup registrar
        hands them to OpenConnector — so a route that merely checked for
        credentials would happily return a URL. Retirement has to be decided by
        the provider being gateway-backed, not by whether a client_id exists.
        """
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            notion_oauth_client_id="notion_client_id",
            notion_oauth_client_secret="notion_secret",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        client = self._client()
        try:
            resp = client.get("/v1/auth/oauth/notion/authorize")
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_notion_callback_is_retired(self):
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            notion_oauth_client_id="notion_client_id",
            notion_oauth_client_secret="notion_secret",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        try:
            client = self._client()
            resp = client.get(
                f"/v1/auth/oauth/notion/callback?code=test_code&state={TEST_USER_ID}",
                follow_redirects=False,
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_unmigrated_provider_still_authorizes(self):
        """Retirement is scoped: atlassian keeps its native authorize URL."""
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            atlassian_oauth_client_id="atlassian_client_id",
            atlassian_oauth_redirect_uri="http://localhost:3000/auth/callback",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        client = self._client()
        try:
            resp = client.get("/v1/auth/oauth/atlassian/authorize")
            assert resp.status_code == 200
            assert "atlassian_client_id" in resp.json()["url"]
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_auth_route_exists(self):
        # Assert via the OpenAPI path map rather than iterating ``app.routes``.
        # Newer Starlette/FastAPI no longer flatten ``include_router`` into
        # ``app.routes`` — they leave ``_IncludedRouter`` wrapper objects with
        # no ``.path``, so ``[r.path for r in app.routes]`` raises
        # AttributeError. ``app.openapi()["paths"]`` resolves the canonical full
        # paths regardless of internal route representation.
        paths = set(app.openapi()["paths"].keys())
        assert "/v1/auth/oauth/{provider}/authorize" in paths
        assert "/v1/auth/oauth/{provider}/callback" in paths
        assert "/v1/auth/magic-link" in paths
        assert "/v1/auth/me" in paths
        assert "/v1/auth/refresh" in paths


class TestAuthServiceRefresh:
    """Unit tests for AuthService.refresh_session."""

    @staticmethod
    def _make_service():
        from unittest.mock import AsyncMock

        from tests.conftest import make_mock_settings

        settings = make_mock_settings(session_ttl_hours=24)
        db = AsyncMock()
        from src.services.auth_service import AuthService

        return AuthService(settings, db), db

    def test_refresh_session_exists(self):
        """AuthService has a refresh_session method."""
        svc, _ = self._make_service()
        assert hasattr(svc, "refresh_session")
        assert callable(svc.refresh_session)


# Raw exception text that must never reach the client. If any of these strings
# show up in a response body, an internal detail has leaked.
_LEAKY_DETAIL = "token sig mismatch for hash a1b2c3 (secret pepper rotated)"


class TestAuthErrorEnvelopeNoLeak:
    """The verify/refresh routes must surface the standard error envelope and
    must NOT leak the raw exception string (str(e)) to the client."""

    def test_verify_does_not_leak_raw_exception(self):
        from unittest.mock import AsyncMock, patch

        with patch("src.api.routes_auth_magic_link.AuthService") as mock_auth:
            mock_auth.return_value.verify_magic_link = AsyncMock(
                side_effect=ValueError(_LEAKY_DETAIL)
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/v1/auth/verify", json={"token": "bad-token"})

        assert resp.status_code == 400
        body = resp.json()
        # Envelope shape, not legacy {"detail": ...}
        assert "error" in body
        assert "detail" not in body
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["correlation_id"]
        # The raw exception text must not appear anywhere in the response.
        assert _LEAKY_DETAIL not in resp.text
        assert "secret pepper" not in resp.text

    def test_refresh_does_not_leak_raw_exception(self):
        from unittest.mock import AsyncMock, patch

        with patch("src.api.routes_auth_session.AuthService") as mock_auth:
            mock_auth.return_value.refresh_session = AsyncMock(
                side_effect=ValueError(_LEAKY_DETAIL)
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/v1/auth/refresh", json={"refresh_token": "bad-token"})

        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert "detail" not in body
        assert body["error"]["code"] == "unauthorized"
        assert body["error"]["correlation_id"]
        assert _LEAKY_DETAIL not in resp.text
        assert "secret pepper" not in resp.text

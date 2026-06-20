"""Tests for OAuth authentication routes."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id
from tests.conftest import TEST_USER_ID


class TestGoogleAuthRoutes:
    """Test Google OAuth flow endpoints."""

    def _client(self):
        _user = MagicMock()
        _user.user_id = TEST_USER_ID
        app.dependency_overrides[get_current_user] = lambda: _user
        app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
        return TestClient(app, raise_server_exceptions=False)

    def _cleanup(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_id, None)

    def test_authorize_url_returns_url(self):
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
            assert resp.status_code == 200
            data = resp.json()
            assert "url" in data
            assert "test_client_id" in data["url"]
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_authorize_url_missing_client_id(self):
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            google_oauth_client_id="",
            google_oauth_client_secret="",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        try:
            client = self._client()
            resp = client.get("/v1/auth/oauth/google/authorize")
            assert resp.status_code in (400, 500)
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_callback_missing_credentials(self):
        from src.config.settings import get_settings
        from tests.conftest import make_mock_settings

        mock_settings = make_mock_settings(
            google_oauth_client_id="",
            google_oauth_client_secret="",
            backend_token="",
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings
        try:
            client = self._client()
            resp = client.get(
                f"/v1/auth/oauth/google/callback?code=test_code&state={TEST_USER_ID}",
                follow_redirects=False,
            )
            # Missing credentials → _error_redirect (307 to frontend with error)
            assert resp.status_code in (307, 400, 500)
        finally:
            app.dependency_overrides.pop(get_settings, None)
            self._cleanup()

    def test_auth_route_exists(self):
        routes = [r.path for r in app.routes]
        assert "/v1/auth/oauth/{provider}/authorize" in routes
        assert "/v1/auth/oauth/{provider}/callback" in routes
        assert "/v1/auth/magic-link" in routes
        assert "/v1/auth/me" in routes
        assert "/v1/auth/refresh" in routes


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

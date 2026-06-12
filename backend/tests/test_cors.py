"""Tests for CORS configuration.

CORS must use an explicit allow-list of methods and headers (no wildcard `*`),
so the browser only grants cross-origin access to the verbs and headers the
frontend actually uses.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import make_mock_settings

ALLOWED_ORIGIN = "https://app.example.com"


def _build_client() -> TestClient:
    settings = make_mock_settings(cors_allowed_origins=ALLOWED_ORIGIN)
    with patch("src.api.app.get_settings", return_value=settings):
        from src.api.app import create_app

        app = create_app()
    return TestClient(app)


def _preflight(client: TestClient, method: str = "POST", request_headers: str = "authorization"):
    return client.options(
        "/v1/health",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": request_headers,
        },
    )


def test_preflight_allows_configured_origin():
    """A preflight from the configured origin is granted."""
    resp = _preflight(_build_client())
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_allowed_methods_are_explicit_not_wildcard():
    """Allow-Methods must enumerate verbs, never `*`."""
    resp = _preflight(_build_client(), method="POST")
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "*" not in allow_methods
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert verb in allow_methods


def test_allowed_headers_cover_what_the_frontend_sends():
    """The headers the frontend actually sends are granted."""
    resp = _preflight(_build_client(), request_headers="authorization,content-type")
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "*" not in allow_headers
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers


def test_unlisted_request_header_is_not_granted():
    """An arbitrary header outside the allow-list is rejected.

    This is what distinguishes an explicit allow-list from `allow_headers=["*"]`:
    a wildcard echoes back any requested header, an explicit list does not.
    """
    resp = _preflight(_build_client(), request_headers="x-arbitrary-injected-header")
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "x-arbitrary-injected-header" not in allow_headers

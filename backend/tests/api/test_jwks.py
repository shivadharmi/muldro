"""Tests for the JWKS endpoint that lets ToolHive validate the platform JWT."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import make_mock_settings


def _build_client() -> TestClient:
    settings = make_mock_settings()
    with patch("src.api.app.get_settings", return_value=settings):
        from src.api.app import create_app

        app = create_app()
    return TestClient(app)


def test_jwks_endpoint_returns_platform_signing_key():
    """GET /.well-known/jwks.json returns the platform's public signing key."""
    resp = _build_client().get("/.well-known/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["keys"][0]["kid"] == "muldro-platform-1"

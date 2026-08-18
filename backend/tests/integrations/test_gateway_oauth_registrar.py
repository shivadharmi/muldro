"""Gateway OAuth client-config registration against OpenConnector."""

from __future__ import annotations

import httpx
import pytest

from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.services.openconnector_admin_client import (
    OpenConnectorAdminClient,
    OpenConnectorAdminError,
)


def test_every_gateway_provider_declares_an_oauth_credential_key():
    """The settings prefix is declared on the registry, never derived downstream.

    Deriving it from server_name ("google-workspace" -> "google") would be a
    hidden transformation that silently breaks when a provider is added.
    """
    keys = {p.provider_id: p.oauth_credential_key for p in PROVIDER_REGISTRY.values()}
    assert keys == {
        "gmail": "google",
        "googlecalendar": "google",
        "github": "github",
    }


async def test_put_oauth_config_sends_credentials_and_returns_body(monkeypatch):
    """PUT /api/oauth/configs/{service} carries clientId/clientSecret and the admin token."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "service": "gmail",
                "configured": True,
                "clientId": "cid",
                "expectedRedirectUri": "http://localhost:3001/oauth/callback",
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("src.services.openconnector_admin_client.httpx.AsyncClient", _factory)

    admin = OpenConnectorAdminClient(base_url="http://oc.test", admin_token="admtok")
    result = await admin.put_oauth_config(service="gmail", client_id="cid", client_secret="csecret")

    assert seen["method"] == "PUT"
    assert seen["url"] == "http://oc.test/api/oauth/configs/gmail"
    assert seen["auth"] == "Bearer admtok"
    assert '"clientId": "cid"' in seen["body"] or '"clientId":"cid"' in seen["body"]
    assert '"clientSecret": "csecret"' in seen["body"] or '"clientSecret":"csecret"' in seen["body"]
    assert result["expectedRedirectUri"] == "http://localhost:3001/oauth/callback"


async def test_put_oauth_config_raises_with_oc_message_on_non_2xx(monkeypatch):
    """A non-2xx carries OpenConnector's own body, not a decoded-JSON crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":{"code":"bad_client"}}')

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("src.services.openconnector_admin_client.httpx.AsyncClient", _factory)

    admin = OpenConnectorAdminClient(base_url="http://oc.test", admin_token="admtok")
    with pytest.raises(OpenConnectorAdminError, match="bad_client"):
        await admin.put_oauth_config(service="gmail", client_id="c", client_secret="s")

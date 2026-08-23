"""Gateway OAuth client-config registration against OpenConnector."""

from __future__ import annotations

import httpx
import pytest

from src.config.settings import Settings
from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.integrations.gateway_oauth_registrar import register_gateway_oauth_configs
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
        "notion": "notion",
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


class FakeAdmin:
    """Records put_oauth_config calls; optionally fails on a chosen service."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._fail_on = fail_on

    async def put_oauth_config(self, *, service: str, client_id: str, client_secret: str) -> dict:
        self.calls.append((service, client_id, client_secret))
        if self._fail_on == service:
            raise OpenConnectorAdminError(f"put_oauth_config({service}) failed: 400 nope")
        return {
            "service": service,
            "configured": True,
            "clientId": client_id,
            "expectedRedirectUri": "http://localhost:3001/oauth/callback",
        }


def _settings(**overrides) -> Settings:
    base = dict(
        openconnector_admin_url="http://oc.test",
        openconnector_admin_token="admtok",
        google_oauth_client_id="g-id",
        google_oauth_client_secret="g-secret",
        github_oauth_client_id="gh-id",
        github_oauth_client_secret="gh-secret",
        notion_oauth_client_id="n-id",
        notion_oauth_client_secret="n-secret",
        skip_gateway_validation=False,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


async def test_registers_every_provider_with_the_right_credentials():
    """google credentials fan out to BOTH gmail and googlecalendar; github gets its own."""
    admin = FakeAdmin()
    registered = await register_gateway_oauth_configs(_settings(), admin=admin)

    assert registered == ["gmail", "googlecalendar", "github", "notion"]
    assert admin.calls == [
        ("gmail", "g-id", "g-secret"),
        ("googlecalendar", "g-id", "g-secret"),
        ("github", "gh-id", "gh-secret"),
        ("notion", "n-id", "n-secret"),
    ]


async def test_never_registers_non_gateway_providers():
    """atlassian has OAuth settings but is not a gateway provider.

    Notion used to be the second example here and is now a counter-example: it
    holds the same shape of settings it always did, and registering it became
    correct the moment it entered PROVIDER_REGISTRY. Having settings is not what
    makes a provider gateway-backed; being in the registry is.
    """
    admin = FakeAdmin()
    await register_gateway_oauth_configs(_settings(), admin=admin)
    assert "atlassian" not in {service for service, _, _ in admin.calls}
    assert {service for service, _, _ in admin.calls} == {
        p.provider_id for p in PROVIDER_REGISTRY.values()
    }


async def test_skip_flag_makes_it_a_no_op():
    admin = FakeAdmin()
    registered = await register_gateway_oauth_configs(
        _settings(skip_gateway_validation=True), admin=admin
    )
    assert registered == []
    assert admin.calls == []


async def test_unconfigured_admin_plane_aborts_before_any_call():
    admin = FakeAdmin()
    with pytest.raises(RuntimeError, match="openconnector_admin_url"):
        await register_gateway_oauth_configs(
            _settings(openconnector_admin_url="", openconnector_admin_token=""), admin=admin
        )
    assert admin.calls == []


async def test_missing_credentials_names_the_offending_provider():
    admin = FakeAdmin()
    with pytest.raises(RuntimeError, match="github"):
        await register_gateway_oauth_configs(
            _settings(github_oauth_client_id="", github_oauth_client_secret=""), admin=admin
        )


async def test_failed_put_propagates_openconnectors_message():
    admin = FakeAdmin(fail_on="googlecalendar")
    with pytest.raises(RuntimeError, match="googlecalendar"):
        await register_gateway_oauth_configs(_settings(), admin=admin)


async def test_secrets_never_reach_the_logs(caplog):
    admin = FakeAdmin()
    with caplog.at_level("INFO"):
        await register_gateway_oauth_configs(_settings(), admin=admin)
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert "g-secret" not in combined
    assert "gh-secret" not in combined
    assert "http://localhost:3001/oauth/callback" in combined

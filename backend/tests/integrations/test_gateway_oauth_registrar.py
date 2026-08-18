"""Gateway OAuth client-config registration against OpenConnector."""

from __future__ import annotations

from src.integrations.gateway_actions import PROVIDER_REGISTRY


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

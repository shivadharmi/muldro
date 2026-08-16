"""Pure tests for the server_name -> OpenConnector-provider gateway mapping."""

from src.integrations.gateway_providers import gateway_oc_provider


def test_google_workspace_maps_to_gmail_when_flag_on():
    assert gateway_oc_provider("google-workspace", gmail_via_gateway=True) == "gmail"


def test_returns_none_when_flag_off():
    assert gateway_oc_provider("google-workspace", gmail_via_gateway=False) is None


def test_unknown_server_returns_none():
    assert gateway_oc_provider("slack", gmail_via_gateway=True) is None
    assert gateway_oc_provider("github", gmail_via_gateway=True) is None


def test_integration_status_dataclass_has_oc_provider_field():
    # Wiring guard: the DTO carries the field the frontend branches on.
    from dataclasses import fields

    from src.services.integration_status import IntegrationStatus

    assert "oc_provider" in {f.name for f in fields(IntegrationStatus)}

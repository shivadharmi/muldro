"""Pure tests for the server_name -> OpenConnector-provider gateway mapping."""

from src.integrations.gateway_providers import gateway_oc_provider

_VMCP = "http://localhost:8100/mcp"


def test_google_workspace_maps_to_gmail_when_flag_and_vmcp_set():
    assert (
        gateway_oc_provider("google-workspace", gmail_via_gateway=True, toolhive_vmcp_url=_VMCP)
        == "gmail"
    )


def test_returns_none_when_flag_off():
    assert (
        gateway_oc_provider("google-workspace", gmail_via_gateway=False, toolhive_vmcp_url=_VMCP)
        is None
    )


def test_returns_none_when_vmcp_url_unset():
    # Mirrors mcp_pool._installation_to_config's third condition: no vMCP url
    # means Gmail tool calls route natively, so the connect flow must be native too.
    assert (
        gateway_oc_provider("google-workspace", gmail_via_gateway=True, toolhive_vmcp_url=None)
        is None
    )


def test_unknown_server_returns_none():
    assert gateway_oc_provider("slack", gmail_via_gateway=True, toolhive_vmcp_url=_VMCP) is None
    assert gateway_oc_provider("github", gmail_via_gateway=True, toolhive_vmcp_url=_VMCP) is None


def test_integration_status_dataclass_has_oc_provider_field():
    # Wiring guard: the DTO carries the field the frontend branches on.
    from dataclasses import fields

    from src.services.integration_status import IntegrationStatus

    assert "oc_provider" in {f.name for f in fields(IntegrationStatus)}

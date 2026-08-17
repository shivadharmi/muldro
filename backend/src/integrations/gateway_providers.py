"""Which MCP installations are served by the OpenConnector gateway, and as
which OC provider.

This is the frontend/back-end seam for choosing the *connect flow*: an
installation with a non-``None`` gateway provider uses the popup-poll
OpenConnector connect flow (``/v1/connections/begin`` + ``/confirm``); a
``None`` result uses the native OAuth-redirect flow. Increment 1 maps only
Gmail (behind the ``gmail_via_gateway`` flag, mirroring
``mcp_pool._installation_to_config``). Increment 2 grows ``_GATEWAY_SERVERS``
to googlecalendar/github/notion/slack/atlassian as they migrate to OC.
"""

from __future__ import annotations

# server_name (IntegrationInstallation.server_name) -> OpenConnector service id.
# Gmail is gated by the gmail_via_gateway flag (see gateway_oc_provider).
_GATEWAY_SERVERS: dict[str, str] = {
    "google-workspace": "gmail",
}


def gateway_oc_provider(
    server_name: str, *, gmail_via_gateway: bool, toolhive_vmcp_url: str | None
) -> str | None:
    """Return the OC provider for ``server_name``, or ``None`` if not gateway-backed.

    Mirrors ALL THREE conditions of the gateway-redirect check in
    ``mcp_pool._installation_to_config`` (``gmail_via_gateway`` AND
    ``server_name == "google-workspace"`` AND a configured ``toolhive_vmcp_url``)
    so the frontend's connect-flow decision can never diverge from the backend's
    actual tool-routing decision. Fail-closed: unknown server, flag off, or no
    vMCP url all yield ``None`` (native connect flow). Increment 2 grows
    ``_GATEWAY_SERVERS``; when it adds another flagged provider, replace the
    gmail-specific guard with a per-provider flag lookup.
    """
    provider = _GATEWAY_SERVERS.get(server_name)
    if provider == "gmail" and not (gmail_via_gateway and toolhive_vmcp_url):
        return None
    return provider

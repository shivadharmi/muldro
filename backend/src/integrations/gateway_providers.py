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


def gateway_oc_provider(server_name: str, *, gmail_via_gateway: bool) -> str | None:
    """Return the OC provider for ``server_name``, or ``None`` if not gateway-backed.

    Fail-closed: an unknown server, or Gmail while the flag is off, yields
    ``None`` (native connect flow). Mirrors the redirect condition in
    ``mcp_pool._installation_to_config`` so the frontend and the tool-routing
    seam agree on which installations are gateway-backed.
    """
    provider = _GATEWAY_SERVERS.get(server_name)
    if provider == "gmail" and not gmail_via_gateway:
        return None
    return provider

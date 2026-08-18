"""httpx client over OpenConnector's admin (`/api/*`) control plane.

Distinct from ``openconnector_client`` (the runtime-token MCP wrapper the
adapter uses to execute actions): the connect-account flow drives the OAuth
authorization + connection lifecycle, which lives on the admin surface gated
by ``openconnector_admin_token`` (see ``infra/gateway/spike-findings-connect.md``).
"""

from __future__ import annotations

import httpx


class OpenConnectorAdminError(Exception):
    """Raised when an admin API call returns a non-2xx response."""


class OpenConnectorAdminClient:
    def __init__(self, *, base_url: str, admin_token: str, timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {admin_token}"}
        self._timeout = timeout

    async def start_authorization(self, *, service: str, connection_name: str) -> dict:
        """POST /api/oauth/authorizations — returns {service, authorizationUrl, state}."""
        body = {"service": service, "connectionName": connection_name}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/api/oauth/authorizations", json=body, headers=self._headers
            )
        if resp.status_code // 100 != 2:
            # resp.text (not resp.json()) — an infra-level error (502/504 HTML,
            # upstream reset) has no JSON body, and decoding it here would raise
            # inside the error path and mask the real status code.
            raise OpenConnectorAdminError(
                f"start_authorization failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()

    async def put_oauth_config(self, *, service: str, client_id: str, client_secret: str) -> dict:
        """PUT /api/oauth/configs/{service} — register the OAuth CLIENT for a service.

        Returns {service, configured, clientId, expectedRedirectUri, auth}.
        Idempotent: re-PUTting the same credentials is a no-op upstream.
        """
        body = {"clientId": client_id, "clientSecret": client_secret}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.put(
                f"{self._base_url}/api/oauth/configs/{service}",
                json=body,
                headers=self._headers,
            )
        if resp.status_code // 100 != 2:
            # resp.text (not resp.json()) — same reason as start_authorization:
            # an infra-level error has no JSON body and decoding it here would
            # raise inside the error path and mask the real status code.
            raise OpenConnectorAdminError(
                f"put_oauth_config({service}) failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()

    async def list_connections(self) -> list[dict]:
        """GET /api/connections — ALL connections on the shared instance."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self._base_url}/api/connections", headers=self._headers)
        if resp.status_code // 100 != 2:
            raise OpenConnectorAdminError(
                f"list_connections failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()

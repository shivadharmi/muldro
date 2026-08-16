"""Connect-account flow — Jarvis owns connection naming.

Jarvis mints the namespaced ``connectionName`` up front and passes it into
OpenConnector's authorization request, so credentials are stored under exactly
the name the adapter later forces (deterministic — no discovery/polling drift).
Completion is poll-based: OpenConnector does NOT redirect back to a Jarvis URL
(``infra/gateway/spike-findings-connect.md`` §4), so ``confirm_connection``
polls ``GET /api/connections`` for the connection becoming ``configured`` and
flips ``connection_map`` ``pending -> active``. The adapter's resolver already
denies any non-``active`` connection, so the flow is fail-closed until confirmed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from src.services.openconnector_admin_client import OpenConnectorAdminClient


def mint_connection_name(tenant_id: str, principal_id: str, provider: str, alias: str) -> str:
    """The canonical namespaced connectionName (matches what the adapter forces)."""
    return f"{tenant_id}:{principal_id}:{provider}:{alias}"


def _default_admin_client() -> OpenConnectorAdminClient:
    s = get_settings()
    if not s.openconnector_admin_url or not s.openconnector_admin_token:
        raise RuntimeError("openconnector_admin_url/openconnector_admin_token are not configured")
    return OpenConnectorAdminClient(
        base_url=s.openconnector_admin_url, admin_token=s.openconnector_admin_token
    )


class ConnectionService:
    def __init__(self, admin_client: OpenConnectorAdminClient | None = None) -> None:
        self._admin = admin_client or _default_admin_client()

    async def begin_connection(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        principal_id: str,
        provider: str,
        alias: str,
    ) -> str:
        """Mint the name, start OAuth authorization, upsert pending, return the consent URL."""
        name = mint_connection_name(workspace_id, principal_id, provider, alias)
        result = await self._admin.start_authorization(service=provider, connection_name=name)

        row = (
            await db.execute(
                select(ConnectionMap).where(
                    ConnectionMap.tenant_id == workspace_id,
                    ConnectionMap.principal_id == principal_id,
                    ConnectionMap.provider_id == provider,
                    ConnectionMap.account_alias == alias,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                ConnectionMap(
                    tenant_id=workspace_id,
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                    provider_id=provider,
                    connection_id=name,
                    connection_status="pending",
                    account_alias=alias,
                )
            )
        else:
            row.connection_id = name
            row.connection_status = "pending"
        return result["authorizationUrl"]

    async def confirm_connection(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        principal_id: str,
        provider: str,
        alias: str,
    ) -> bool:
        """Poll OpenConnector; flip pending -> active if the connection is configured.

        Returns True if the connection is (now) active, False if still pending.
        """
        name = mint_connection_name(workspace_id, principal_id, provider, alias)
        connections = await self._admin.list_connections()
        configured = any(
            c.get("connectionName") == name and c.get("configured") is True for c in connections
        )
        row = (
            await db.execute(
                select(ConnectionMap).where(
                    ConnectionMap.tenant_id == workspace_id,
                    ConnectionMap.principal_id == principal_id,
                    ConnectionMap.provider_id == provider,
                    ConnectionMap.account_alias == alias,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        if configured and row.connection_status != "active":
            row.connection_status = "active"
        return row.connection_status == "active"

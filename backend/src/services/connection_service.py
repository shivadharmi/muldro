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

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.integrations.gateway_actions import perception_sources_for_provider
from src.models.connection_map import ConnectionMap
from src.services.openconnector_admin_client import (
    OpenConnectorAdminClient,
    OpenConnectorAdminError,
)

logger = logging.getLogger(__name__)


def mint_connection_name(tenant_id: str, principal_id: str, provider: str, alias: str) -> str:
    """The canonical OC-valid connectionName for an identity tuple.

    OpenConnector v1.3.5 rejects a connectionName outside ``[A-Za-z0-9_-]`` and
    caps it at 64 chars; a colon-joined ``{tenant}:{principal}:{provider}:{alias}``
    both violates the charset and (with 26-char ULIDs) blows the length. A
    blake2b digest of the tuple is a stable, collision-negligible (80-bit),
    20-char hex string that satisfies the rule. It is never reversed:
    ``connection_map`` stores it and the adapter's resolver reads the stored
    value (``adapter/connection_resolver.py``), so opacity is fine. Determinism
    keeps ``begin`` and ``confirm`` in agreement without shared state, and makes
    reconnecting the same alias idempotent (OC refreshes creds under one name).
    """
    raw = f"{tenant_id}:{principal_id}:{provider}:{alias}".encode()
    return hashlib.blake2b(raw, digest_size=10).hexdigest()


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
        authorization_url = result.get("authorizationUrl")
        if not authorization_url:
            raise OpenConnectorAdminError(
                f"authorization response missing authorizationUrl: {result}"
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
        elif row.connection_status != "active":
            # Restart a pending/failed connection. An ALREADY-ACTIVE row is left
            # untouched: a stray re-begin (double-click, re-auth) must not demote
            # a live connection to pending and make the resolver start denying it.
            # (connection_id is deterministic, so it never changes for this key.)
            row.connection_id = name
            row.connection_status = "pending"
        return authorization_url

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
            c.get("connectionName") == name
            and c.get("configured") is True
            and c.get("service", provider) == provider
            for c in connections
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
        # ONLY the pending -> active edge promotes. Two distinct reasons:
        #   - A repeat confirm (polling, a re-connect of an already-live account)
        #     must not re-enable schedules the user has since turned off.
        #   - A `revoked` row must never be promoted here. Disconnect flips the
        #     local row but leaves the OpenConnector-side credential in place, so
        #     `configured` stays True indefinitely — promoting on it would let a
        #     session cookie plus (provider, alias) undo a disconnect with no OAuth
        #     screen and no user intent. Re-connecting goes through
        #     `begin_connection`, which starts a real authorization before it moves
        #     the row back to `pending`.
        if configured and row.connection_status == "pending":
            row.connection_status = "active"
            await self._enable_perception_schedules(db, workspace_id, provider)
        return row.connection_status == "active"

    @staticmethod
    async def _enable_perception_schedules(
        db: AsyncSession, workspace_id: str, provider: str
    ) -> None:
        """Turn on the observe_* schedules this OC provider's sources feed.

        Native OAuth connects do this through
        ``routes_auth_oauth_integration._enable_integration_schedules``, but the
        Google/GitHub callbacks that used to reach it were deleted with their
        OAuth branches — those brands now arrive HERE instead. Without this, a
        correctly connected workspace never gets its observe_* schedules enabled
        and never provisions the PerceptionState rows the scheduler polls, so
        perception silently never starts. WHICH sources a provider backs is read
        from the gateway registry, never restated here.

        Non-fatal by construction: the writes run inside a SAVEPOINT, so a
        seeding failure rolls back only the schedule changes and can neither fail
        nor poison the caller's transaction — the connection activation on the
        same session still commits.
        """
        sources = perception_sources_for_provider(provider)
        if not sources:
            return
        try:
            from src.services.schedule_seeder import enable_schedules_for_connector

            async with db.begin_nested():
                for source in sources:
                    await enable_schedules_for_connector(db, source, workspace_id=workspace_id)
        except Exception:
            logger.warning(
                "Failed to enable perception schedules for %s in %s",
                provider,
                workspace_id,
                exc_info=True,
            )

"""Connection resolver — the alias-ownership + namespacing core of the
adapter's tenant boundary.

Given a verified ``AdapterPrincipal`` and a requested provider + account
alias, resolves the namespaced ``connection_id`` the principal owns — or
raises ``ConnectionDenied`` if no such active connection exists. This is the
only place that turns "principal wants provider X, alias Y" into a concrete
connection identity; it never trusts a caller-supplied connection_id
directly, only (tenant_id, principal_id, provider_id, account_alias).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapter.identity import AdapterPrincipal
from src.models.connection_map import DEFAULT_ACCOUNT_ALIAS, ConnectionMap


class ConnectionDenied(Exception):  # noqa: N818 - "denied", not "error": a policy refusal
    """No active connection owned by this principal for the requested alias."""


async def resolve_connection(
    db: AsyncSession,
    principal: AdapterPrincipal,
    *,
    provider_id: str,
    account_alias: str | None,
) -> str:
    alias = account_alias or DEFAULT_ACCOUNT_ALIAS
    row = (
        await db.execute(
            select(ConnectionMap).where(
                ConnectionMap.tenant_id == principal.tenant_id,
                ConnectionMap.principal_id == principal.principal_id,
                ConnectionMap.provider_id == provider_id,
                ConnectionMap.account_alias == alias,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConnectionDenied(f"no connection for alias '{alias}'")
    if row.connection_status != "active":
        raise ConnectionDenied(f"connection '{alias}' is {row.connection_status}")
    return row.connection_id

"""Adapter server — composes the six-step enforcement for one gateway call.

This module is the sole tenant boundary for the gateway: every inbound tool
call is verified (identity), allowlisted (action), resolved to an OWNED
connection (never caller-supplied), forced onto the outbound OpenConnector
call, and the response is normalized + secret-stripped before it ever reaches
the caller. One adapter process now serves a reviewed SET of providers (spec
decision D2), so the enforcement profile is resolved PER-ACTION from the
action_id itself, never from a process-level setting. See
``src.adapter.identity``, ``src.adapter.connection_resolver``, and
``src.adapter.enforcement`` for the individual pieces this composes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapter.connection_resolver import resolve_connection
from src.adapter.enforcement import (
    ensure_action_allowed,
    ensure_capability_allowed,
    force_connection_name,
    profile_for_action,
    strip_secrets,
)
from src.adapter.identity import verify_principal
from src.adapter.openconnector_client import call_openconnector
from src.models.connection_map import ConnectionMap


def _result_to_dict(result: Any) -> dict:
    """Normalize a fastmcp tool result to a plain dict BEFORE secret-stripping.

    ``strip_secrets`` only recurses into dict/list; a ``CallToolResult``
    object would pass through un-stripped and could leak secrets in its
    attributes. Prefer structured content, then data, then serialized
    content blocks.
    """
    if isinstance(result, dict):
        return result
    for attr in ("structured_content", "structuredContent", "data"):
        val = getattr(result, attr, None)
        if isinstance(val, dict):
            return val
    content = getattr(result, "content", None)
    if content is not None:
        items = []
        for block in content:
            if hasattr(block, "model_dump"):
                items.append(block.model_dump())
            elif isinstance(block, dict):
                items.append(block)
            else:
                items.append({"text": str(getattr(block, "text", block))})
        return {"content": items}
    return {"result": str(result)}


async def handle_execute_action(db: AsyncSession, *, token: str, args: dict) -> dict:
    """Six-step enforcement for one ``execute_action`` call (the sole tenant boundary)."""
    principal = verify_principal(token)  # 1. identity (never from args)
    action_id = args.get("actionId", "")
    # 5a. Resolve the policy surface FROM THE ACTION, not from a process-level
    # setting: one adapter serves several providers, so the profile -- and the
    # provider whose connection is resolved below -- must follow the action.
    # Fail-closed: an unregistered actionId raises ActionNotAllowed here, before
    # any DB or network work.
    profile = profile_for_action(action_id)
    ensure_action_allowed(action_id, profile)  # 5a. allowlist actionId (fail fast)
    ensure_capability_allowed(  # 5b. principal authorized for THIS action
        action_id, principal.capabilities, profile
    )
    forced = await resolve_connection(  # 2. resolve OWNED connection
        db,
        principal,
        provider_id=profile.provider_id,
        account_alias=args.get("account_alias"),
    )
    outbound = force_connection_name(args, forced)  # 3. force connectionName
    outbound.pop("account_alias", None)  # do not forward the Jarvis hint
    result = await call_openconnector("execute_action", outbound)  # 6a. forward
    return strip_secrets(_result_to_dict(result))  # 6b. normalize + strip secrets


async def handle_list_connections(db: AsyncSession, *, token: str) -> dict:
    """4. Suppress global enumeration — return only THIS principal's connections.

    Never calls OpenConnector's ``list_connections`` (which enumerates ALL
    accounts on the shared instance).
    """
    principal = verify_principal(token)
    rows = (
        (
            await db.execute(
                select(ConnectionMap).where(
                    ConnectionMap.tenant_id == principal.tenant_id,
                    ConnectionMap.principal_id == principal.principal_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "connections": [
            {
                "account_alias": r.account_alias,
                "provider": r.provider_id,
                "status": r.connection_status,
            }
            for r in rows
        ]
    }

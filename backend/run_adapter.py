"""Entrypoint for the OpenConnector gateway Connection Context Adapter.

Exposes the generic ``execute_action`` / ``list_connections`` tools plus one
named tool per allowlisted OpenConnector action across EVERY provider in the
registry (gmail, googlecalendar, github, ...), warm-started from OC's
``get_action_guide`` at startup. The bearer token carrying the caller's
platform JWT is read from the HTTP Authorization header via the shared
``bearer_token`` helper (never from tool args).

Run with: ``python run_adapter.py`` (from ``backend/``).
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import FastMCP

from src.adapter.enforcement import get_gateway_profile
from src.adapter.http_context import bearer_token
from src.adapter.openconnector_client import get_action_guide
from src.adapter.server import handle_execute_action, handle_list_connections
from src.adapter.warm_start import register_gateway_tools
from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.models.database import get_session_factory

logger = logging.getLogger(__name__)

adapter = FastMCP("openconnector-gateway-adapter")

_HOST = "0.0.0.0"  # noqa: S104 - intentional: gateway must be reachable off-host
_PORT = 8100


@adapter.tool()
async def execute_action(
    actionId: str,  # noqa: N803 - matches OpenConnector's camelCase tool schema
    input: dict,
    account_alias: str | None = None,
) -> dict:
    """Execute an allowlisted gateway action through the caller's owned connection."""
    token = bearer_token()
    args = {"actionId": actionId, "input": input}
    if account_alias is not None:
        args["account_alias"] = account_alias
    async with get_session_factory()() as db:
        return await handle_execute_action(db, token=token, args=args)


@adapter.tool()
async def list_connections() -> dict:
    """List only the calling principal's own connections (never global enumeration)."""
    token = bearer_token()
    async with get_session_factory()() as db:
        return await handle_list_connections(db, token=token)


async def warm_start() -> int:
    """Register named per-action tools for every provider in the registry."""
    total = 0
    for provider_id in PROVIDER_REGISTRY:
        profile = get_gateway_profile(provider_id)
        total += await register_gateway_tools(adapter, profile, guide_fetcher=get_action_guide)
    logger.info(
        "warm-start: registered %d named gateway tools across %d providers",
        total,
        len(PROVIDER_REGISTRY),
    )
    return total


if __name__ == "__main__":
    asyncio.run(warm_start())
    adapter.run(transport="http", host=_HOST, port=_PORT)

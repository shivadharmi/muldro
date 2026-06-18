"""Lazy, one-shot MCP tool-schema discovery.

When tool exposure is decoupled from eager startup discovery, the first agent
build for a server whose DB tool records lack ``input_schema`` triggers a
single discovery pass for that server. The discovered schemas are persisted to
the DB by the pool, so subsequent builds read them straight from the registry.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from src.integrations.mcp_pool import get_workspace_pool

logger = logging.getLogger(__name__)


async def discover_missing_schemas(
    tool_defs: Iterable[Any],
    *,
    workspace_id: str,
) -> set[str]:
    """Discover + persist schemas for external servers whose tools lack one.

    Returns the set of server names a discovery pass was run for. Failures are
    swallowed (logged) so a flaky server never blocks agent construction.
    """
    pool = get_workspace_pool()
    if pool is None:
        return set()

    servers_missing: set[str] = set()
    for t in tool_defs:
        server = getattr(t, "server", None)
        if server and not getattr(t, "input_schema", None):
            servers_missing.add(server)

    discovered: set[str] = set()
    for server in servers_missing:
        if pool.is_discovered(server, workspace_id=workspace_id):
            continue
        try:
            await pool.discover_and_persist(server, workspace_id=workspace_id)
            discovered.add(server)
        except Exception:
            logger.debug("Lazy discovery failed for %s", server, exc_info=True)
    return discovered

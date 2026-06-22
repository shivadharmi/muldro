"""Check that host runtimes needed to spawn MCP servers are present.

We no longer run MCP servers in Docker — stdio servers run via ``npx`` and the
Google Workspace server runs via ``uvx``. Missing runtimes are not fatal at
startup (an MCP call will surface a structured error), but we log a loud
warning so operators notice before a user hits it.
"""

from __future__ import annotations

import logging
import shutil

logger = logging.getLogger(__name__)


def check_mcp_runtimes(required: list[str]) -> list[str]:
    """Return the subset of required runtimes not found on PATH."""
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        logger.warning(
            "[mcp:preflight] missing host runtime(s): %s — MCP servers needing "
            "them will fail until installed (npx=Node, uvx=uv)",
            ", ".join(missing),
        )
    return missing

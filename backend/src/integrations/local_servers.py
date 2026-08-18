"""Launch specs for locally-managed HTTP MCP servers, derived from settings.

Replicates the old infra/docker/google-workspace-mcp/entrypoint.sh env mapping
(MULDRO_-prefixed settings -> the names workspace-mcp expects) but spawns the
server as a host process via uvx instead of a Docker container.
"""

from __future__ import annotations

from typing import Any

from src.integrations.local_process_manager import LocalServerSpec

# Pinned to the version resolved during the infra hardening pass (2026-06-18).
# Bump intentionally when upgrading workspace-mcp.
WORKSPACE_MCP_PACKAGE = "workspace-mcp==1.21.3"


def build_local_server_specs(settings: Any) -> dict[str, LocalServerSpec]:
    """Return {server_name: LocalServerSpec} for all locally-managed servers."""
    google = LocalServerSpec(
        server_name="google-workspace",
        argv=[
            "uvx",
            WORKSPACE_MCP_PACKAGE,
            "--transport",
            "streamable-http",
            "--tool-tier",
            "complete",
            "--tools",
            "gmail",
            "calendar",
        ],
        env={
            "MCP_ENABLE_OAUTH21": "true",
            "EXTERNAL_OAUTH21_PROVIDER": "true",
            "WORKSPACE_MCP_STATELESS_MODE": "true",
            "GOOGLE_OAUTH_CLIENT_ID": settings.google_oauth_client_id or "",
            "GOOGLE_OAUTH_CLIENT_SECRET": settings.google_oauth_client_secret or "",
        },
        path="/mcp",
        port_env_var="WORKSPACE_MCP_PORT",
    )
    return {"google-workspace": google}

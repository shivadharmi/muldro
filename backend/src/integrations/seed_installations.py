"""Seed default IntegrationInstallation records for a workspace.

These mirror what was previously hardcoded in mcp_config.py but are now
workspace-scoped DB rows managed by the IntegrationControlPlane.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ids import generate_id
from src.models.integration_installation import IntegrationInstallation
from src.models.server_trust import ServerTrustRecord

logger = logging.getLogger(__name__)

_DEFAULT_FILESYSTEM_ROOT = str(Path.home() / "jarvis-workspace")


def _filesystem_mcp_root() -> str:
    """Root directory exposed to the filesystem MCP server.

    Configurable via JARVIS_FILESYSTEM_MCP_ROOT; defaults to ~/jarvis-workspace
    (persists across reboots, unlike /tmp on many systems).
    """
    return os.environ.get("JARVIS_FILESYSTEM_MCP_ROOT", "") or _DEFAULT_FILESYSTEM_ROOT


async def _clear_stale_tool_schemas(db: AsyncSession, server_name: str, workspace_id: str) -> None:
    """Clear cached input_schema for tools belonging to a server.

    Called when the server's transport changes (e.g., stdio → HTTP), because
    tool schemas may differ between transport modes (OAuth 2.0 vs 2.1).
    """
    from sqlalchemy import update

    from src.models.tool_definitions import ToolDefinition

    await db.execute(
        update(ToolDefinition)
        .where(
            ToolDefinition.server == server_name,
            ToolDefinition.input_schema.isnot(None),
        )
        .values(input_schema=None)
    )
    logger.info("Cleared stale tool schemas for server %s", server_name)


# Default installations — each maps to a former get_*_config() function
_DEFAULT_INSTALLATIONS: list[dict] = [
    {
        "server_name": "google-workspace",
        "display_name": "Google Workspace",
        "transport": "streamable-http",
        "remote_url": os.environ.get(
            "JARVIS_GOOGLE_WORKSPACE_MCP_URL", "http://localhost:8001/mcp"
        ),
        "command": None,
        "args": None,
        "env_template": {},
        "auth_provider": "google",
        "scopes_granted": [
            "email.send",
            "email.list",
            "email.read",
            "email.search",
            "email.draft",
            "calendar.list",
            "calendar.get",
            "calendar.create",
            "calendar.update",
            "doc.drive_list",
            "doc.drive_search",
            "doc.drive_create",
        ],
    },
    {
        "server_name": "github",
        "display_name": "GitHub",
        "transport": "streamable-http",
        "remote_url": "https://api.githubcopilot.com/mcp/",
        "command": None,
        "args": None,
        "env_template": {},
        "auth_provider": "github",
        "scopes_granted": [
            "issue.create",
            "issue.list",
            "issue.search",
            "issue.comment",
            "repo.create_pr",
            "repo.merge_pr",
            "repo.search_code",
            "repo.search_repos",
            "repo.list_prs",
        ],
    },
    {
        "server_name": "slack",
        "display_name": "Slack",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "slack-mcp-server"],
        "env_template": {
            "SLACK_MCP_XOXP_TOKEN": "Slack user OAuth token (xoxp-...)",
            "SLACK_MCP_XOXB_TOKEN": "Slack bot OAuth token (xoxb-...)",
        },
        "auth_provider": "token",
        "scopes_granted": [
            "messaging.send",
            "messaging.reply",
            "messaging.react",
            "messaging.list_channels",
            "messaging.get_history",
            "messaging.search",
        ],
    },
    {
        "server_name": "playwright",
        "display_name": "Playwright Browser",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp", "--headless"],
        "env_template": {},
        "auth_provider": None,
        "scopes_granted": [
            "browser.open",
            "browser.snapshot",
            "browser.extract",
            "browser.click",
            "browser.type",
            "browser.submit",
            "browser.screenshot",
        ],
    },
    {
        "server_name": "filesystem",
        "display_name": "Filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", _filesystem_mcp_root()],
        "env_template": {},
        "auth_provider": None,
        "scopes_granted": [],
    },
    {
        "server_name": "notion",
        "display_name": "Notion",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env_template": {
            "NOTION_TOKEN": "Notion integration token",
        },
        "auth_provider": "notion",
        "scopes_granted": [
            "doc.create",
            "doc.update",
            "doc.get",
            "doc.search",
            "doc.comment",
            "doc.append",
        ],
    },
    {
        "server_name": "atlassian",
        "display_name": "Atlassian (Jira + Confluence)",
        "transport": "streamable-http",
        "remote_url": "https://mcp.atlassian.com/v1/mcp",
        "command": None,
        "args": None,
        "env_template": {},
        "auth_provider": "atlassian",
        "scopes_granted": [
            # Jira
            "issue.create",
            "issue.update",
            "issue.search",
            "issue.comment",
            "issue.transition",
            # Confluence
            "doc.create",
            "doc.update",
            "doc.get",
            "doc.search",
            "doc.comment",
            "doc.list",
        ],
    },
]


async def seed_installations(db: AsyncSession, workspace_id: str, user_id: str) -> int:
    """Seed or update default integration installations. Returns count created/updated.

    For existing installations, syncs transport, command, args, scopes_granted,
    and env_template from defaults so code changes propagate on restart.
    Does NOT change enabled/status (user controls those).
    """
    # Build trust_id lookup
    trust_result = await db.execute(
        select(ServerTrustRecord).where(ServerTrustRecord.workspace_id == workspace_id)
    )
    trust_by_name: dict[str, str] = {
        r.server_name: r.trust_id for r in trust_result.scalars().all()
    }

    # Build lookup of existing installations
    inst_result = await db.execute(
        select(IntegrationInstallation).where(
            IntegrationInstallation.workspace_id == workspace_id,
        )
    )
    existing = {inst.server_name: inst for inst in inst_result.scalars().all()}

    changed = 0
    for inst_data in _DEFAULT_INSTALLATIONS:
        server_name = inst_data["server_name"]

        if server_name not in existing:
            installation = IntegrationInstallation(
                install_id=generate_id("inst"),
                workspace_id=workspace_id,
                user_id=user_id,
                server_name=server_name,
                display_name=inst_data["display_name"],
                transport=inst_data.get("transport", "stdio"),
                command=inst_data.get("command"),
                args=inst_data.get("args"),
                env_template=inst_data.get("env_template"),
                remote_url=inst_data.get("remote_url"),
                trust_id=trust_by_name.get(server_name),
                auth_provider=inst_data.get("auth_provider"),
                scopes_granted=inst_data.get("scopes_granted"),
            )
            db.add(installation)
            changed += 1
            continue

        # Sync mutable fields (never touch enabled/status — user controls those)
        inst = existing[server_name]
        needs_update = False

        if inst.transport != inst_data.get("transport", "stdio"):
            inst.transport = inst_data.get("transport", "stdio")
            needs_update = True

        # HTTP servers get schemas from live discovery — always clear stale
        # DB schemas so they don't override live ones.  This handles both
        # transport changes and schema drift (e.g., OAuth 2.1 mode changes).
        if inst_data.get("transport", "stdio") in ("sse", "streamable-http"):
            await _clear_stale_tool_schemas(db, server_name, workspace_id)
        if inst.remote_url != inst_data.get("remote_url"):
            inst.remote_url = inst_data.get("remote_url")
            needs_update = True
        if inst.command != inst_data.get("command"):
            inst.command = inst_data.get("command")
            needs_update = True
        if inst.args != inst_data.get("args"):
            inst.args = inst_data.get("args")
            needs_update = True
        if inst.auth_provider != inst_data.get("auth_provider"):
            inst.auth_provider = inst_data.get("auth_provider")
            needs_update = True
        if inst.scopes_granted != inst_data.get("scopes_granted"):
            inst.scopes_granted = inst_data.get("scopes_granted")
            needs_update = True
        if inst.env_template != inst_data.get("env_template"):
            inst.env_template = inst_data.get("env_template")
            needs_update = True
        # Update trust_id if it was populated later
        expected_trust = trust_by_name.get(server_name)
        if expected_trust and inst.trust_id != expected_trust:
            inst.trust_id = expected_trust
            needs_update = True

        # Backfill tool_defaults for Atlassian installations connected before
        # auto-injection existed. If the OAuth callback already stored
        # cloud_id but no tool_defaults, we can derive the map now so every
        # future MCP call gets it auto-injected without re-linking.
        if server_name == "atlassian" and isinstance(inst.config, dict):
            stored_cloud = inst.config.get("cloud_id")
            existing_defaults = inst.config.get("tool_defaults") or {}
            if stored_cloud and existing_defaults.get("cloudId") != stored_cloud:
                new_cfg = dict(inst.config)
                new_cfg["tool_defaults"] = {
                    **existing_defaults,
                    "cloudId": stored_cloud,
                }
                inst.config = new_cfg
                needs_update = True

        if needs_update:
            changed += 1

    # Remove stale installations that are no longer in _DEFAULT_INSTALLATIONS
    default_names = {i["server_name"] for i in _DEFAULT_INSTALLATIONS}
    for server_name, inst in existing.items():
        if server_name not in default_names:
            await db.delete(inst)
            changed += 1
            logger.info("Removed stale installation: %s", server_name)

    if changed:
        await db.flush()
        logger.info("Seeded/updated %d integration installations", changed)
    return changed

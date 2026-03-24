"""Seed default IntegrationInstallation records for a workspace.

These mirror what was previously hardcoded in mcp_config.py but are now
workspace-scoped DB rows managed by the IntegrationControlPlane.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.integration_installation import IntegrationInstallation
from src.models.ids import generate_id
from src.models.server_trust import ServerTrustRecord

logger = logging.getLogger(__name__)

# Default installations — each maps to a former get_*_config() function
_DEFAULT_INSTALLATIONS: list[dict] = [
    {
        "server_name": "google-workspace",
        "display_name": "Google Workspace",
        "transport": "stdio",
        "command": "uvx",
        "args": ["google-workspace-mcp"],
        "env_template": {
            "GOOGLE_OAUTH_CREDENTIALS_PATH": "Path to OAuth credentials JSON",
            "GOOGLE_OAUTH_TOKEN_PATH": "Path to OAuth token JSON",
        },
        "auth_provider": "oauth",
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
        "transport": "stdio",
        "command": "docker",
        "args": [
            "run",
            "-i",
            "--rm",
            "-e",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "ghcr.io/github/github-mcp-server",
        ],
        "env_template": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "GitHub personal access token",
        },
        "auth_provider": "token",
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
            "SLACK_BOT_TOKEN": "Slack bot OAuth token",
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
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/jarvis-workspace"],
        "env_template": {},
        "auth_provider": None,
        "scopes_granted": [],
    },
    {
        "server_name": "linear",
        "display_name": "Linear",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-server-linear"],
        "env_template": {
            "LINEAR_ACCESS_TOKEN": "Linear API access token",
        },
        "auth_provider": "token",
        "scopes_granted": [
            "workflow.create_issue",
            "workflow.update_issue",
            "workflow.comment",
            "workflow.list",
            "workflow.search",
        ],
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
        "auth_provider": "token",
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
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-remote@latest", "https://mcp.atlassian.com/v1/mcp"],
        "env_template": {
            "JARVIS_ATLASSIAN_MCP_ENABLED": "Set to 'true' to enable",
        },
        "auth_provider": "oauth",
        "scopes_granted": [
            "issue.create",
            "issue.update",
            "issue.search",
            "issue.comment",
            "issue.transition",
        ],
    },
    {
        "server_name": "twilio",
        "display_name": "Twilio (SMS/Voice)",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@twilio-alpha/mcp"],
        "env_template": {
            "TWILIO_ACCOUNT_SID": "Twilio account SID",
            "TWILIO_AUTH_TOKEN": "Twilio auth token",
        },
        "auth_provider": "token",
        "scopes_granted": ["messaging.send"],
    },
]


async def seed_installations(db: AsyncSession, workspace_id: str, user_id: str) -> int:
    """Seed default connector installations. Returns count created."""
    # Build trust_id lookup
    trust_result = await db.execute(
        select(ServerTrustRecord).where(ServerTrustRecord.workspace_id == workspace_id)
    )
    trust_by_name: dict[str, str] = {
        r.server_name: r.trust_id for r in trust_result.scalars().all()
    }

    created = 0
    for inst_data in _DEFAULT_INSTALLATIONS:
        server_name = inst_data["server_name"]
        existing = await db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.workspace_id == workspace_id,
                IntegrationInstallation.server_name == server_name,
            )
        )
        if existing.scalar_one_or_none():
            continue

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
            trust_id=trust_by_name.get(server_name),
            auth_provider=inst_data.get("auth_provider"),
            scopes_granted=inst_data.get("scopes_granted"),
        )
        db.add(installation)
        created += 1

    if created:
        await db.flush()
        logger.info("Seeded %d connector installations", created)
    return created

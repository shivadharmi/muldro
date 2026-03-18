"""MCP server configurations for external connectors.

Defines how to launch and connect to external MCP servers
(Google Workspace, GitHub, Slack, Playwright, Perplexity).
These are configured but only activated when credentials are available.
"""

import logging
import os

logger = logging.getLogger(__name__)


def get_google_workspace_config() -> dict | None:
    """Google Workspace MCP server configuration.

    Requires: GOOGLE_OAUTH_CREDENTIALS_PATH env var pointing to OAuth credentials.
    Provides: gmail_*, calendar_*, drive_*, docs_*, sheets_*, tasks_*, contacts_*
    """
    creds_path = os.environ.get("GOOGLE_OAUTH_CREDENTIALS_PATH")
    if not creds_path:
        logger.info("Google Workspace MCP not configured (no GOOGLE_OAUTH_CREDENTIALS_PATH)")
        return None
    return {
        "name": "google-workspace",
        "transport": "stdio",
        "command": "uvx",
        "args": ["google-workspace-mcp"],
        "env": {
            "GOOGLE_OAUTH_CREDENTIALS_PATH": creds_path,
            "GOOGLE_OAUTH_TOKEN_PATH": os.environ.get(
                "GOOGLE_OAUTH_TOKEN_PATH", os.path.expanduser("~/.jarvis/google_token.json")
            ),
        },
    }


def get_github_config() -> dict | None:
    """GitHub MCP server configuration.

    Requires: GITHUB_TOKEN env var.
    Provides: repo search, code analysis, PR review, implementation patterns.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("JARVIS_GITHUB_TOKEN")
    if not token:
        logger.info("GitHub MCP not configured (no GITHUB_TOKEN)")
        return None
    return {
        "name": "github",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }


def get_slack_config() -> dict | None:
    """Slack MCP server configuration.

    Requires: SLACK_BOT_TOKEN env var.
    Provides: messages, threads, search, channels, reactions, user groups, DMs.
    """
    token = os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("JARVIS_SLACK_BOT_TOKEN")
    if not token:
        logger.info("Slack MCP not configured (no SLACK_BOT_TOKEN)")
        return None
    return {
        "name": "slack",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "slack-mcp-server"],
        "env": {"SLACK_BOT_TOKEN": token},
    }


def get_playwright_config() -> dict | None:
    """Playwright MCP server configuration.

    No credentials required.
    Provides: browser automation (navigate, click, fill, screenshot, extract text).
    """
    return {
        "name": "playwright",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp", "--headless"],
    }


def get_filesystem_config() -> dict | None:
    """Official Filesystem MCP server configuration.

    Provides: read/write/edit files, directory ops, search.
    Restricted to specific paths for safety.
    """
    workspace = os.environ.get("JARVIS_WORKSPACE_PATH", "/tmp/jarvis-workspace")
    os.makedirs(workspace, exist_ok=True)
    return {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", workspace],
    }


def get_available_mcp_configs() -> list[dict]:
    """Return configs for all available MCP servers (those with valid credentials)."""
    configs = []
    for getter in [
        get_google_workspace_config,
        get_github_config,
        get_slack_config,
        get_playwright_config,
        get_filesystem_config,
    ]:
        config = getter()
        if config:
            configs.append(config)
    return configs

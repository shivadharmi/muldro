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
    """GitHub MCP server configuration (official github/github-mcp-server).

    Requires: GITHUB_TOKEN env var.
    Go binary via Docker (ghcr.io/github/github-mcp-server).
    Tools: issue_read, issue_write, list_issues, search_issues, pull_request_read,
    create_pull_request, merge_pull_request, search_code, search_repositories, etc.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("JARVIS_GITHUB_TOKEN")
    if not token:
        logger.info("GitHub MCP not configured (no GITHUB_TOKEN)")
        return None
    return {
        "name": "github",
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


def get_linear_config() -> dict | None:
    """Linear MCP server configuration (mcp-server-linear by dvcrn).

    Requires: LINEAR_ACCESS_TOKEN or JARVIS_LINEAR_ACCESS_TOKEN env var.
    Tools (24): linear_create_issue, linear_edit_issue, linear_search_issues,
    linear_get_issue, linear_create_comment, linear_get_teams, etc.
    Note: Tools already carry a ``linear_`` prefix from the server itself.
    """
    token = (
        os.environ.get("LINEAR_ACCESS_TOKEN")
        or os.environ.get("JARVIS_LINEAR_ACCESS_TOKEN")
        or os.environ.get("LINEAR_API_KEY")
        or os.environ.get("JARVIS_LINEAR_API_KEY")
    )
    if not token:
        return None
    return {
        "name": "linear",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-server-linear"],
        "env": {"LINEAR_ACCESS_TOKEN": token},
    }


def get_notion_config() -> dict | None:
    """Notion MCP server configuration (official @notionhq/notion-mcp-server).

    Requires: NOTION_TOKEN or JARVIS_NOTION_TOKEN env var.
    Tools (22, kebab-case): create-a-page, retrieve-a-page, update-a-page,
    search, query-data-source, create-a-comment, append-block-children, etc.
    """
    token = (
        os.environ.get("NOTION_TOKEN")
        or os.environ.get("JARVIS_NOTION_TOKEN")
        or os.environ.get("NOTION_API_KEY")
        or os.environ.get("JARVIS_NOTION_API_KEY")
    )
    if not token:
        return None
    return {
        "name": "notion",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"NOTION_TOKEN": token},
    }


def get_jira_config() -> dict | None:
    """Atlassian Rovo MCP server (official).

    Remote MCP at https://mcp.atlassian.com/v1/mcp, proxied via mcp-remote.
    Auth: OAuth 2.1 (interactive browser) — no static env var needed.
    Jira tools (13): createJiraIssue, editJiraIssue, getJiraIssue,
    searchJiraIssuesUsingJql, transitionJiraIssue, addCommentToJiraIssue, etc.
    Also provides Confluence + Compass tools.
    """
    enabled = os.environ.get("JARVIS_ATLASSIAN_MCP_ENABLED", "")
    if not enabled:
        return None
    return {
        "name": "atlassian",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-remote@latest", "https://mcp.atlassian.com/v1/mcp"],
    }


def get_twilio_config() -> dict | None:
    """Twilio MCP server configuration (official @twilio-alpha/mcp).

    Requires: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN env vars.
    Tools: Dynamic from OpenAPI (Messaging, Voice, Conversations, etc.).
    """
    sid = os.environ.get("TWILIO_ACCOUNT_SID") or os.environ.get("JARVIS_TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN") or os.environ.get("JARVIS_TWILIO_AUTH_TOKEN")
    if not all([sid, token]):
        return None
    return {
        "name": "twilio",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@twilio-alpha/mcp"],
        "env": {"TWILIO_ACCOUNT_SID": sid, "TWILIO_AUTH_TOKEN": token},
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
        get_linear_config,
        get_notion_config,
        get_jira_config,  # Atlassian Rovo MCP
        get_twilio_config,
    ]:
        config = getter()
        if config:
            configs.append(config)
    return configs

"""Tool name normalizer — unifies 6 naming conventions into canonical snake_case.

MCP servers use different naming conventions:
- Google Workspace MCP: camelCase (sendGmailDraft, listCalendarEvents)
- Notion MCP: kebab-case (create-a-page, query-data-source)
- GitHub MCP: snake_case (issue_write, create_pull_request)
- Slack MCP: snake_case prefixed (slack_reply_to_thread)
- FastMCP namespaced: {server_key}_{tool_name} (google_workspace_sendGmailDraft)
- Internal Jarvis: snake_case (search, ingest_event)

This module normalizes all names to canonical snake_case and maintains a
bidirectional map for dispatching (canonical → raw MCP name).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case.

    Examples:
        sendGmailDraft → send_gmail_draft
        createJiraIssue → create_jira_issue
        listCalendarEvents → list_calendar_events
    """
    # Insert underscore before uppercase letters that follow lowercase or digits
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Insert underscore between consecutive uppercase and lowercase (e.g. "GmailD" → "Gmail_D")
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


def kebab_to_snake(name: str) -> str:
    """Convert kebab-case to snake_case.

    Examples:
        create-a-page → create_a_page
        query-data-source → query_data_source
    """
    return name.replace("-", "_")


def strip_server_prefix(raw_name: str, server_name: str) -> str:
    """Strip the FastMCP server namespace prefix from a tool name.

    FastMCP namespaces tools as {server_key}_{tool_name} in multi-server configs.
    The server_key normalizes hyphens to underscores.

    Examples:
        strip_server_prefix("google_workspace_sendGmailDraft", "google-workspace")
        → "sendGmailDraft"
    """
    prefix = server_name.replace("-", "_") + "_"
    if raw_name.startswith(prefix):
        return raw_name[len(prefix) :]
    return raw_name


class ToolNameNormalizer:
    """Normalizes tool names and maintains bidirectional mapping.

    Usage:
        normalizer = ToolNameNormalizer()
        normalizer.register_alias("sendGmailDraft", "send_gmail_draft")

        canonical = normalizer.normalize(
            "google_workspace_sendGmailDraft", server_name="google-workspace",
        )
        # → "send_gmail_draft"

        raw = normalizer.to_raw("send_gmail_draft", server_name="google-workspace")
        # → "google_workspace_sendGmailDraft"
    """

    def __init__(self, extra_aliases: dict[str, str] | None = None) -> None:
        # canonical_name → raw_mcp_name (per server)
        self._canonical_to_raw: dict[str, dict[str, str]] = {}
        # raw_mcp_name → canonical_name
        self._raw_to_canonical: dict[str, str] = {}
        # Extra static aliases (from CANONICAL_ALIASES in tool_registry)
        self._static_aliases: dict[str, str] = dict(extra_aliases) if extra_aliases else {}

    def normalize(self, raw_name: str, server_name: str | None = None) -> str:
        """Normalize any tool name to canonical snake_case form.

        Steps:
        1. Check existing raw→canonical map
        2. Strip server prefix if present
        3. Convert camelCase → snake_case
        4. Convert kebab-case → snake_case
        5. Apply static aliases
        """
        # Fast path: already mapped
        if raw_name in self._raw_to_canonical:
            return self._raw_to_canonical[raw_name]

        name = raw_name

        # Strip server prefix
        if server_name:
            name = strip_server_prefix(name, server_name)

        # Convert camelCase → snake_case
        if any(c.isupper() for c in name):
            name = camel_to_snake(name)

        # Convert kebab-case → snake_case
        if "-" in name:
            name = kebab_to_snake(name)

        # Apply static aliases
        name = self._static_aliases.get(name, name)

        return name

    def register_server_tools(
        self,
        server_name: str,
        tools: list[dict],
    ) -> dict[str, str]:
        """Register all tools from an MCP server, building bidirectional maps.

        Args:
            server_name: The MCP server name (e.g., "google-workspace")
            tools: List of tool dicts with "name" key (from list_tools())

        Returns:
            Dict of {canonical_name: raw_mcp_name} for the registered tools.
        """
        mapping: dict[str, str] = {}

        for tool in tools:
            raw_name = tool["name"] if isinstance(tool, dict) else tool.name
            canonical = self.normalize(raw_name, server_name=server_name)

            # Store bidirectional mapping
            self._raw_to_canonical[raw_name] = canonical
            if server_name not in self._canonical_to_raw:
                self._canonical_to_raw[server_name] = {}
            self._canonical_to_raw[server_name][canonical] = raw_name
            mapping[canonical] = raw_name

            logger.debug("Registered tool: %s → %s (server: %s)", raw_name, canonical, server_name)

        return mapping

    def to_raw(self, canonical_name: str, server_name: str) -> str | None:
        """Resolve a canonical name back to the raw MCP tool name for a server.

        Returns None if not found.
        """
        server_map = self._canonical_to_raw.get(server_name, {})
        return server_map.get(canonical_name)

    def register_alias(self, raw_name: str, canonical_name: str) -> None:
        """Register a single raw→canonical alias."""
        self._raw_to_canonical[raw_name] = canonical_name

    def get_all_canonical_names(self) -> list[str]:
        """Return all known canonical tool names."""
        return list(set(self._raw_to_canonical.values()))

    def get_server_tools(self, server_name: str) -> dict[str, str]:
        """Return {canonical: raw} mapping for a specific server."""
        return dict(self._canonical_to_raw.get(server_name, {}))


# Module-level singleton, initialized with aliases from ToolRegistry.
_normalizer: ToolNameNormalizer | None = None


def get_normalizer() -> ToolNameNormalizer:
    """Get or create the global ToolNameNormalizer singleton."""
    global _normalizer
    if _normalizer is None:
        from src.services.tool_registry import CANONICAL_ALIASES

        _normalizer = ToolNameNormalizer(extra_aliases=CANONICAL_ALIASES)
    return _normalizer

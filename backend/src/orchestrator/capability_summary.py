"""Capability summary generator for Planner prompts.

Produces a compact (~200 token) XML summary of connected and disconnected
services from the tool registry and installation status. Replaces ~15-20K
tokens of raw tool schemas in Planner system prompts.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.integration_installation import IntegrationInstallation
from src.models.tool_definitions import ToolDefinition
from src.tools.catalog import EXTERNAL_TOOL_SEEDS

# ── Display names for capability families ─────────────────────────────

_FAMILY_DISPLAY: dict[str, str] = {
    "email": "email — Gmail",
    "calendar": "calendar — Google Calendar",
    "repo": "repo — GitHub",
    "issue": "issue — GitHub/Atlassian",
    "doc": "doc — Notion/Drive/Atlassian",
    "workflow": "workflow — Atlassian",
    "messaging": "messaging — Slack",
    "browser": "browser — Playwright",
    "search": "search — Web",
    "filesystem": "filesystem — Local Files",
    "internal": "internal",
    "system": "system",
}


def _family_display_name(family: str) -> str:
    """Return human-readable display name for a capability family prefix.

    Falls back to the raw family string for unknown families.
    """
    return _FAMILY_DISPLAY.get(family, family)


def _group_by_family(tools: list) -> dict[str, list[str]]:
    """Group tools by capability family prefix, collecting unique action names.

    Tools with ``capability=None`` are skipped. Actions are deduplicated
    within each family.

    Example::

        tools with capabilities "email.search", "email.read", "email.send"
        → {"email": ["search", "read", "send"]}
    """
    families: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    for tool in tools:
        cap = tool.capability
        if cap is None:
            continue
        parts = cap.split(".", 1)
        if len(parts) != 2:
            continue
        family, action = parts
        if family not in families:
            families[family] = []
            seen[family] = set()
        if action not in seen[family]:
            seen[family].add(action)
            families[family].append(action)

    return families


def _get_seed_server_names() -> list[str]:
    """Return sorted unique server names from external tool seeds.

    Excludes servers starting with ``_`` (e.g. ``_composite``).
    """
    servers: set[str] = set()
    for seed in EXTERNAL_TOOL_SEEDS:
        if not seed.server.startswith("_"):
            servers.add(seed.server)
    return sorted(servers)


async def generate_capability_summary(db: AsyncSession, workspace_id: str) -> str:
    """Generate a compact capability summary for the Planner prompt.

    Queries enabled tools and active installations, then produces an
    XML-formatted string with ``<connected_services>`` and optionally
    ``<disconnected_services>`` sections.
    """
    # Fetch enabled tools
    tool_result = await db.execute(select(ToolDefinition).where(ToolDefinition.enabled.is_(True)))
    tools = tool_result.scalars().all()

    # Fetch active installations for this workspace
    inst_result = await db.execute(
        select(IntegrationInstallation).where(
            IntegrationInstallation.workspace_id == workspace_id,
            IntegrationInstallation.status == "active",
            IntegrationInstallation.enabled.is_(True),
        )
    )
    installations = inst_result.scalars().all()

    # Group tools by capability family, excluding internal.*
    all_families = _group_by_family(tools)
    families = {k: v for k, v in all_families.items() if k != "internal"}

    # Build <connected_services> section
    # Format: "prefix — Description: prefix.action1, prefix.action2"
    # Using fully-qualified capability names prevents the Planner from
    # inventing wrong prefixes (e.g. "notion.search" instead of "doc.search").
    lines: list[str] = ["<connected_services>"]
    for family, actions in sorted(families.items()):
        display = _family_display_name(family)
        sorted_actions = sorted(actions)
        qualified = ", ".join(f"{family}.{a}" for a in sorted_actions)
        lines.append(f"  {display}: {qualified}")
    lines.append("</connected_services>")

    # Determine disconnected services
    active_servers = {inst.server_name for inst in installations}
    seed_servers = _get_seed_server_names()
    disconnected = sorted(s for s in seed_servers if s not in active_servers)

    if disconnected:
        lines.append("<disconnected_services>")
        for server in disconnected:
            lines.append(f"  {server}")
        lines.append("</disconnected_services>")

    return "\n".join(lines)

"""Seed default trust records and capability bindings for a workspace."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    CapabilityFamily,
    get_family_for_capability,
)
from src.models.capability_binding import CapabilityBinding
from src.models.ids import generate_id
from src.models.server_trust import ServerTrustRecord
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

# Default T0/T1 trust records
_DEFAULT_TRUST_RECORDS: list[dict] = [
    {
        "server_name": "intelligence-server",
        "trust_tier": "T0",
        "verified_by": "system",
        "status": "active",
    },
    {
        "server_name": "communication-server",
        "trust_tier": "T0",
        "verified_by": "system",
        "status": "active",
    },
    {
        "server_name": "google-workspace",
        "trust_tier": "T1",
        "verified_by": "anthropic",
        "status": "active",
    },
    {
        "server_name": "github",
        "trust_tier": "T1",
        "verified_by": "anthropic",
        "status": "active",
    },
    {
        "server_name": "slack",
        "trust_tier": "T1",
        "verified_by": "anthropic",
        "status": "active",
    },
    {
        "server_name": "playwright",
        "trust_tier": "T1",
        "verified_by": "microsoft",
        "status": "active",
    },
    {
        "server_name": "linear",
        "trust_tier": "T1",
        "verified_by": "linear",
        "status": "active",
    },
    {
        "server_name": "notion",
        "trust_tier": "T1",
        "verified_by": "notion",
        "status": "active",
    },
    {
        "server_name": "atlassian",
        "trust_tier": "T1",
        "verified_by": "atlassian",
        "status": "active",
    },
]

# Server → connector type mapping for binding generation
_SERVER_TO_CONNECTORS: dict[str, list[str]] = {
    "intelligence-server": ["internal"],
    "communication-server": ["telegram"],
    "google-workspace": ["gmail", "calendar", "drive"],
    "github": ["github"],
    "slack": ["slack"],
    "playwright": ["browser"],
    "linear": ["linear"],
    "notion": ["notion"],
    "atlassian": ["jira"],
}


async def seed_trust_records(db: AsyncSession, workspace_id: str) -> list[ServerTrustRecord]:
    """Seed or update default trust records. Returns created/updated records.

    For existing records, syncs trust_tier, verified_by, and status
    from defaults so code changes propagate on restart.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(ServerTrustRecord).where(ServerTrustRecord.workspace_id == workspace_id)
    )
    existing = {r.server_name: r for r in result.scalars().all()}

    changed = []
    for rec_data in _DEFAULT_TRUST_RECORDS:
        name = rec_data["server_name"]

        if name not in existing:
            record = ServerTrustRecord(
                trust_id=generate_id("trs"),
                workspace_id=workspace_id,
                **rec_data,
            )
            db.add(record)
            changed.append(record)
            continue

        # Sync mutable fields
        record = existing[name]
        needs_update = False
        if record.trust_tier != rec_data["trust_tier"]:
            record.trust_tier = rec_data["trust_tier"]
            needs_update = True
        if record.verified_by != rec_data.get("verified_by"):
            record.verified_by = rec_data.get("verified_by")
            needs_update = True
        if record.status != rec_data.get("status", "active"):
            record.status = rec_data.get("status", "active")
            needs_update = True

        if needs_update:
            changed.append(record)

    if changed:
        await db.flush()
    return changed


async def seed_capability_bindings(db: AsyncSession, workspace_id: str) -> list[CapabilityBinding]:
    """Seed or update capability bindings from tool catalog.

    For existing bindings, syncs priority, backend_ref, and trust_id
    from current defaults so code changes propagate on restart.
    """
    from sqlalchemy import select

    # Build trust_id lookup
    result = await db.execute(
        select(ServerTrustRecord).where(ServerTrustRecord.workspace_id == workspace_id)
    )
    trust_by_server: dict[str, str] = {r.server_name: r.trust_id for r in result.scalars().all()}

    # Build connector → server mapping (reverse of _SERVER_TO_CONNECTORS)
    connector_to_server: dict[str, str] = {}
    for server, connectors in _SERVER_TO_CONNECTORS.items():
        for conn in connectors:
            connector_to_server[conn] = server

    # Load existing bindings for this workspace
    existing_result = await db.execute(
        select(CapabilityBinding).where(CapabilityBinding.workspace_id == workspace_id)
    )
    existing_map: dict[tuple[str, str], CapabilityBinding] = {
        (b.capability, b.backend_type): b for b in existing_result.scalars().all()
    }

    # Build tool_name → capability mapping from catalog
    tool_to_capability: dict[str, str] = {}
    for tool in INTERNAL_TOOLS:
        tool_to_capability[tool.name] = tool.capability
    for seed in EXTERNAL_TOOL_SEEDS:
        tool_to_capability[seed.name] = seed.capability

    changed = []
    seen_caps: set[tuple[str, str]] = set()  # (capability, backend_type)

    for tool_name, capability in tool_to_capability.items():
        family = get_family_for_capability(capability)
        if not family:
            continue

        cap_meta = CAPABILITY_CATALOG.get(capability)
        if not cap_meta:
            continue

        connector_type = _infer_connector(tool_name, family)
        server_name = connector_to_server.get(connector_type, "")
        trust_id = trust_by_server.get(server_name)

        is_internal = server_name in ("intelligence-server", "communication-server")
        backend_type = "native" if is_internal else "mcp_official"

        key = (capability, backend_type)
        if key in seen_caps:
            continue
        seen_caps.add(key)

        expected_ref = server_name or connector_type
        expected_priority = 10 if backend_type == "native" else 50

        if key not in existing_map:
            binding = CapabilityBinding(
                binding_id=generate_id("capb"),
                workspace_id=workspace_id,
                capability=capability,
                family=str(family),
                backend_type=backend_type,
                backend_ref=expected_ref,
                tool_name=tool_name,
                priority=expected_priority,
                enabled=True,
                trust_id=trust_id,
            )
            db.add(binding)
            changed.append(binding)
            continue

        # Sync mutable fields
        binding = existing_map[key]
        needs_update = False
        if binding.backend_ref != expected_ref:
            binding.backend_ref = expected_ref
            needs_update = True
        if binding.priority != expected_priority:
            binding.priority = expected_priority
            needs_update = True
        if trust_id and binding.trust_id != trust_id:
            binding.trust_id = trust_id
            needs_update = True

        if needs_update:
            changed.append(binding)

    if changed:
        await db.flush()
    return changed


def _infer_connector(tool_name: str, family: CapabilityFamily) -> str:
    """Infer connector type from tool name prefix or family."""
    prefixes = {
        "gmail_": "gmail",
        "sendGmail": "gmail",
        "createGmail": "gmail",
        "listGmail": "gmail",
        "readGmail": "gmail",
        "searchGmail": "gmail",
        "deleteGmail": "gmail",
        "calendar_": "calendar",
        "createCalendar": "calendar",
        "updateCalendar": "calendar",
        "deleteCalendar": "calendar",
        "listCalendar": "calendar",
        "getCalendar": "calendar",
        "github_": "github",
        "slack_": "slack",
        "linear_": "linear",
        "notion_": "notion",
        "jira_": "jira",
        "browser_": "browser",
        "drive_": "drive",
        "whatsapp_": "whatsapp",
        "sms_": "sms",
        "linkedin_": "linkedin",
        "twitter_": "twitter",
        "perplexity_": "browser",
    }
    for prefix, connector in prefixes.items():
        if tool_name.startswith(prefix):
            return connector

    # Jira MCP tools (camelCase)
    if "Jira" in tool_name:
        return "jira"

    # GitHub MCP tools (no prefix)
    github_tools = {
        "issue_write",
        "issue_read",
        "add_issue_comment",
        "create_pull_request",
        "merge_pull_request",
        "update_pull_request",
        "pull_request_read",
        "pull_request_review_write",
        "sub_issue_write",
        "list_issues",
        "search_issues",
        "search_code",
        "search_repositories",
        "search_users",
        "search_orgs",
        "get_diff",
        "get_reviews",
        "get_check_runs",
        "get_files",
        "list_pull_requests",
        "search_pull_requests",
        "get_sub_issues",
    }
    if tool_name in github_tools:
        return "github"

    # Notion MCP (kebab-case)
    notion_tools = {
        "create-a-page",
        "update-a-page",
        "retrieve-a-page",
        "search",
        "query-data-source",
        "create-a-comment",
        "append-block-children",
    }
    if tool_name in notion_tools:
        return "notion"

    # Family-based fallback
    family_to_connector: dict[CapabilityFamily, str] = {
        CapabilityFamily.INTERNAL: "internal",
        CapabilityFamily.BROWSER: "browser",
        CapabilityFamily.SEARCH: "browser",
    }
    return family_to_connector.get(family, "internal")

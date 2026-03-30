"""Seed default trust records and capability bindings for a workspace."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import (
    CapabilityFamily,
)
from src.models.ids import generate_id
from src.models.server_trust import ServerTrustRecord

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

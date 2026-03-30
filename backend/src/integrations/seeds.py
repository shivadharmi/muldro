"""Seed default trust records for a workspace."""

from sqlalchemy.ext.asyncio import AsyncSession

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

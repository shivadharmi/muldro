"""Capability Health Service — user-facing capability status.

Translates system state (integration health, MCP gateway status, native adapter
availability) into a user-understandable capability health grid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import CAPABILITY_CATALOG, CapabilityFamily
from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    """Health status for a single capability family."""

    family: str
    status: str  # healthy, degraded, unavailable, unconfigured
    provider: str | None
    last_activity_at: datetime | None
    capabilities_available: int
    capabilities_total: int
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityHealthReport:
    """Overall capability health for a workspace."""

    healthy_count: int
    degraded_count: int
    unavailable_count: int
    unconfigured_count: int
    families: list[CapabilityStatus]
    last_updated_at: datetime


# Map capability families to their typical providers.
#
# KNOWN DEFECT (pre-existing, needs its own change — do not "fix" one half).
# This one list is consumed by two queries in DIFFERENT namespaces:
#   _check_installation  -> IntegrationInstallation.server_name  (server names:
#                           google-workspace, github, slack, playwright,
#                           notion, atlassian)
#   _get_last_activity   -> NormalizedEvent.source               (source names:
#                           gmail, calendar, github, slack, notion)
# For github/slack/notion/atlassian those two strings are identical, which is
# why the conflation is invisible there. Google is the only installation
# serving two differently-named sources, so "email" and "calendar" match no
# server_name and report status="unconfigured" forever — telling the user
# "Configure gmail to enable" for something they cannot configure under that
# name. Substituting "google-workspace" only moves the failure to the activity
# query. Fixing it properly means splitting this into installation names and
# source names; until then the two entries below are knowingly wrong.
# ("search": ["perplexity"] has no installation at all, same shape.)
FAMILY_PROVIDERS: dict[str, list[str]] = {
    "email": ["gmail"],
    "calendar": ["calendar"],
    "repo": ["github"],
    "issue": ["github", "atlassian"],
    "doc": ["notion", "atlassian"],
    "workflow": ["atlassian"],
    "messaging": ["slack"],
    "browser": ["playwright"],
    "search": ["perplexity"],
    "internal": [],
}


class CapabilityHealthService:
    """Builds capability health reports from system state."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def get_health_report(self) -> CapabilityHealthReport:
        """Build a complete capability health report."""
        families: list[CapabilityStatus] = []

        for family in CapabilityFamily:
            if family == CapabilityFamily.INTERNAL:
                families.append(
                    CapabilityStatus(
                        family=family.value,
                        status="healthy",
                        provider="muldro",
                        last_activity_at=datetime.now(timezone.utc),
                        capabilities_available=self._count_capabilities(family),
                        capabilities_total=self._count_capabilities(family),
                    )
                )
                continue

            status = await self._check_family_health(family)
            families.append(status)

        healthy = sum(1 for f in families if f.status == "healthy")
        degraded = sum(1 for f in families if f.status == "degraded")
        unavailable = sum(1 for f in families if f.status == "unavailable")
        unconfigured = sum(1 for f in families if f.status == "unconfigured")

        return CapabilityHealthReport(
            healthy_count=healthy,
            degraded_count=degraded,
            unavailable_count=unavailable,
            unconfigured_count=unconfigured,
            families=families,
            last_updated_at=datetime.now(timezone.utc),
        )

    async def get_family_status(self, family: str) -> CapabilityStatus | None:
        """Get health status for a specific capability family."""
        try:
            cap_family = CapabilityFamily(family)
        except ValueError:
            return None
        return await self._check_family_health(cap_family)

    async def _check_family_health(self, family: CapabilityFamily) -> CapabilityStatus:
        """Check health for a capability family by examining its providers."""
        providers = FAMILY_PROVIDERS.get(family.value, [])
        total_caps = self._count_capabilities(family)

        if not providers:
            return CapabilityStatus(
                family=family.value,
                status="unconfigured",
                provider=None,
                last_activity_at=None,
                capabilities_available=0,
                capabilities_total=total_caps,
                message="No provider configured for this capability",
            )

        # Check if we have any installation for these providers
        has_installation = await self._check_installation(providers)
        if not has_installation:
            return CapabilityStatus(
                family=family.value,
                status="unconfigured",
                provider=None,
                last_activity_at=None,
                capabilities_available=0,
                capabilities_total=total_caps,
                message=f"Configure {' or '.join(providers)} to enable",
            )

        # Check recent activity
        last_activity, active_provider = await self._get_last_activity(providers)
        now = datetime.now(timezone.utc)

        if last_activity and (now - last_activity).total_seconds() < 86400:
            return CapabilityStatus(
                family=family.value,
                status="healthy",
                provider=active_provider,
                last_activity_at=last_activity,
                capabilities_available=total_caps,
                capabilities_total=total_caps,
            )

        if last_activity and (now - last_activity).total_seconds() < 86400 * 7:
            return CapabilityStatus(
                family=family.value,
                status="degraded",
                provider=active_provider,
                last_activity_at=last_activity,
                capabilities_available=total_caps,
                capabilities_total=total_caps,
                message="No recent activity; connector may need attention",
            )

        return CapabilityStatus(
            family=family.value,
            status="unavailable",
            provider=active_provider,
            last_activity_at=last_activity,
            capabilities_available=0,
            capabilities_total=total_caps,
            message="No activity in 7+ days; check integration health",
        )

    async def _check_installation(self, providers: list[str]) -> bool:
        """Check if any of the given providers have an active installation."""
        try:
            from src.models.integration_installation import IntegrationInstallation

            count = await self._db.scalar(
                select(func.count())
                .select_from(IntegrationInstallation)
                .where(
                    IntegrationInstallation.workspace_id == self._workspace_id,
                    IntegrationInstallation.status == "active",
                    IntegrationInstallation.server_name.in_(providers),
                )
            )
            return (count or 0) > 0
        except Exception:
            return False

    async def _get_last_activity(self, providers: list[str]) -> tuple[datetime | None, str | None]:
        """Get the most recent event from any of the given providers."""
        result = await self._db.execute(
            select(NormalizedEvent.occurred_at, NormalizedEvent.source)
            .where(
                NormalizedEvent.workspace_id == self._workspace_id,
                NormalizedEvent.source.in_(providers),
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(1)
        )
        row = result.first()
        if row:
            return row[0], row[1]
        return None, None

    def _count_capabilities(self, family: CapabilityFamily) -> int:
        """Count capabilities in a family from the catalog."""
        return sum(1 for meta in CAPABILITY_CATALOG.values() if meta.family == family)

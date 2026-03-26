"""Connector Insight Service — sync health, downstream impact, dependency mapping.

Provides rich analytics about connector health, data flow impact,
and inter-connector dependencies for the connectors detail screen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncHealthReport:
    """Health status for a connector's sync operations."""

    provider: str
    status: str  # healthy, degraded, unhealthy, inactive
    events_last_24h: int
    events_last_7d: int
    last_event_at: datetime | None
    avg_events_per_day: float
    error_rate: float  # 0.0 - 1.0
    latency_trend: str  # improving, stable, degrading


@dataclass(frozen=True, slots=True)
class DownstreamImpact:
    """What depends on this connector's data."""

    entities_created: int
    memories_influenced: int
    plans_triggered: int
    briefings_contributed: int
    active_webhook_count: int


@dataclass(frozen=True, slots=True)
class ConnectorDependency:
    """A dependency between connectors."""

    source_provider: str
    target_provider: str
    relationship: str  # triggers, enriches, blocks
    description: str


@dataclass(frozen=True, slots=True)
class ConnectorInsightReport:
    """Full insight report for a connector."""

    provider: str
    sync_health: SyncHealthReport
    downstream_impact: DownstreamImpact
    dependencies: list[ConnectorDependency]
    recent_events: list[dict]
    recommendations: list[str]


class ConnectorInsightService:
    """Builds insight reports for connectors."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def get_insight(self, provider: str) -> ConnectorInsightReport:
        """Build a full insight report for a connector."""
        sync_health = await self.get_sync_health(provider)
        downstream = await self.get_downstream_impact(provider)
        deps = self.get_dependencies(provider)
        recent = await self._get_recent_events(provider)
        recommendations = self._build_recommendations(sync_health, downstream)

        return ConnectorInsightReport(
            provider=provider,
            sync_health=sync_health,
            downstream_impact=downstream,
            dependencies=deps,
            recent_events=recent,
            recommendations=recommendations,
        )

    async def get_sync_health(self, provider: str) -> SyncHealthReport:
        """Analyze sync health for a provider."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        events_24h = (
            await self._db.scalar(
                select(func.count())
                .select_from(NormalizedEvent)
                .where(
                    NormalizedEvent.workspace_id == self._workspace_id,
                    NormalizedEvent.source == provider,
                    NormalizedEvent.occurred_at > day_ago,
                )
            )
            or 0
        )

        events_7d = (
            await self._db.scalar(
                select(func.count())
                .select_from(NormalizedEvent)
                .where(
                    NormalizedEvent.workspace_id == self._workspace_id,
                    NormalizedEvent.source == provider,
                    NormalizedEvent.occurred_at > week_ago,
                )
            )
            or 0
        )

        last_event_result = await self._db.execute(
            select(NormalizedEvent.occurred_at)
            .where(
                NormalizedEvent.workspace_id == self._workspace_id,
                NormalizedEvent.source == provider,
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(1)
        )
        last_event_row = last_event_result.first()
        last_event_at = last_event_row[0] if last_event_row else None

        avg_per_day = events_7d / 7.0 if events_7d else 0.0

        # Determine health status
        status = "healthy"
        if last_event_at and (now - last_event_at).total_seconds() > 86400:
            status = "inactive"
        elif events_24h == 0 and avg_per_day > 1:
            status = "degraded"

        # Latency trend: compare first half of week vs second half
        mid_week = now - timedelta(days=3.5)
        first_half = (
            await self._db.scalar(
                select(func.count())
                .select_from(NormalizedEvent)
                .where(
                    NormalizedEvent.workspace_id == self._workspace_id,
                    NormalizedEvent.source == provider,
                    NormalizedEvent.occurred_at > week_ago,
                    NormalizedEvent.occurred_at <= mid_week,
                )
            )
            or 0
        )
        second_half = (
            await self._db.scalar(
                select(func.count())
                .select_from(NormalizedEvent)
                .where(
                    NormalizedEvent.workspace_id == self._workspace_id,
                    NormalizedEvent.source == provider,
                    NormalizedEvent.occurred_at > mid_week,
                )
            )
            or 0
        )

        if second_half > first_half * 1.2:
            latency_trend = "improving"
        elif second_half < first_half * 0.8:
            latency_trend = "degrading"
        else:
            latency_trend = "stable"

        return SyncHealthReport(
            provider=provider,
            status=status,
            events_last_24h=events_24h,
            events_last_7d=events_7d,
            last_event_at=last_event_at,
            avg_events_per_day=round(avg_per_day, 1),
            error_rate=0.0,
            latency_trend=latency_trend,
        )

    async def get_downstream_impact(self, provider: str) -> DownstreamImpact:
        """Analyze what depends on this connector's data."""
        from src.models.entities import Entity
        from src.models.memory import Memory

        entities_count = (
            await self._db.scalar(
                select(func.count())
                .select_from(Entity)
                .where(
                    Entity.workspace_id == self._workspace_id,
                    Entity.source == provider,
                )
            )
            or 0
        )

        memories_count = (
            await self._db.scalar(
                select(func.count())
                .select_from(Memory)
                .where(
                    Memory.workspace_id == self._workspace_id,
                    Memory.source == provider,
                )
            )
            or 0
        )

        webhook_count = 0
        try:
            from src.models.webhook_subscription import WebhookSubscription

            webhook_count = (
                await self._db.scalar(
                    select(func.count())
                    .select_from(WebhookSubscription)
                    .where(
                        WebhookSubscription.workspace_id == self._workspace_id,
                        WebhookSubscription.provider == provider,
                        WebhookSubscription.status == "active",
                    )
                )
                or 0
            )
        except Exception:
            pass

        return DownstreamImpact(
            entities_created=entities_count,
            memories_influenced=memories_count,
            plans_triggered=0,
            briefings_contributed=0,
            active_webhook_count=webhook_count,
        )

    def get_dependencies(self, provider: str) -> list[ConnectorDependency]:
        """Get known inter-connector dependencies."""
        deps: list[ConnectorDependency] = []

        dependency_map: dict[str, list[tuple[str, str, str]]] = {
            "gmail": [
                ("calendar", "enriches", "Calendar events referenced in emails"),
                ("slack", "triggers", "Slack notifications for important emails"),
            ],
            "calendar": [
                ("gmail", "enriches", "Email threads related to calendar events"),
            ],
            "github": [
                ("slack", "triggers", "Slack notifications for PR/issue updates"),
                ("linear", "enriches", "Linear issues linked to GitHub PRs"),
            ],
            "slack": [
                ("gmail", "enriches", "Email context for Slack discussions"),
            ],
            "linear": [
                ("github", "enriches", "GitHub PRs linked to Linear issues"),
            ],
        }

        for target, rel, desc in dependency_map.get(provider, []):
            deps.append(
                ConnectorDependency(
                    source_provider=provider,
                    target_provider=target,
                    relationship=rel,
                    description=desc,
                )
            )

        return deps

    async def _get_recent_events(self, provider: str, limit: int = 10) -> list[dict]:
        """Get recent events from this provider."""
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.workspace_id == self._workspace_id,
                NormalizedEvent.source == provider,
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(limit)
        )
        events = []
        for e in result.scalars().all():
            events.append(
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                    "importance_score": e.importance_score,
                }
            )
        return events

    def _build_recommendations(
        self, health: SyncHealthReport, impact: DownstreamImpact
    ) -> list[str]:
        """Build actionable recommendations based on health and impact."""
        recs: list[str] = []

        if health.status == "inactive":
            recs.append(
                f"No events from {health.provider} in 24h. "
                "Check credential health or re-authenticate."
            )

        if health.status == "degraded":
            recs.append(
                f"{health.provider} event volume is below normal. "
                "Consider checking for API rate limits."
            )

        if health.latency_trend == "degrading":
            recs.append(
                f"{health.provider} event volume is declining. "
                "This may affect downstream briefings and plans."
            )

        if impact.active_webhook_count == 0 and health.provider in (
            "gmail",
            "github",
            "slack",
            "calendar",
        ):
            recs.append(
                f"Enable push notifications for {health.provider} "
                "to reduce polling overhead and get faster updates."
            )

        if impact.entities_created == 0:
            recs.append(
                f"No entities have been created from {health.provider}. "
                "Ensure the observer is processing events correctly."
            )

        return recs

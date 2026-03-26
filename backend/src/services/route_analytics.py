"""Route analytics service — selection counts, fallbacks, quality.

Provides analytics about routing decisions: which routes are used most,
fallback rates, and quality metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.agent_routes import AgentRoute
from src.models.traces import Trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteUsage:
    route_name: str
    decision_type: str
    agent_pipeline: list[str]
    selection_count_24h: int
    selection_count_7d: int
    success_rate: float
    avg_duration_ms: float
    fallback_count: int


@dataclass(frozen=True)
class RouteFallback:
    trace_id: str
    original_route: str
    fallback_route: str
    reason: str
    occurred_at: datetime


@dataclass(frozen=True)
class RouteQuality:
    route_name: str
    quality_score: float  # 0-100
    factors: list[str]


@dataclass(frozen=True)
class RouteAnalyticsReport:
    routes: list[RouteUsage]
    fallbacks: list[RouteFallback]
    quality: list[RouteQuality]
    total_routes: int
    active_routes: int
    most_used_route: str | None
    lowest_quality_route: str | None


class RouteAnalyticsService:
    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def get_report(self) -> RouteAnalyticsReport:
        """Build a full route analytics report."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        # Get all routes
        route_result = await self._db.execute(
            select(AgentRoute).order_by(AgentRoute.priority.desc())
        )
        all_routes = route_result.scalars().all()
        active_routes = [r for r in all_routes if r.enabled]

        # Get recent traces for route usage
        trace_result = await self._db.execute(
            select(Trace)
            .where(
                Trace.workspace_id == self._workspace_id,
                Trace.started_at >= week_ago,
            )
            .order_by(Trace.started_at.desc())
        )
        traces = trace_result.scalars().all()

        # Map traces to routes based on agents_invoked
        route_traces: dict[str, list[Trace]] = {r.name: [] for r in all_routes}
        for trace in traces:
            agents = trace.agents_invoked or []
            for route in all_routes:
                pipeline = route.agent_pipeline or []
                if isinstance(pipeline, list) and agents:
                    if any(a in agents for a in pipeline):
                        route_traces[route.name].append(trace)
                        break

        # Build route usage
        usages: list[RouteUsage] = []
        for route in all_routes:
            route_trace_list = route_traces.get(route.name, [])
            traces_24h = [t for t in route_trace_list if t.started_at >= day_ago]
            traces_7d = route_trace_list

            completed = [t for t in traces_7d if t.status == "completed"]
            success_rate = len(completed) / len(traces_7d) if traces_7d else 1.0

            avg_duration = (
                sum(t.duration_ms or 0 for t in traces_7d) / len(traces_7d) if traces_7d else 0.0
            )

            pipeline = route.agent_pipeline if isinstance(route.agent_pipeline, list) else []

            usages.append(
                RouteUsage(
                    route_name=route.name,
                    decision_type=route.decision_type,
                    agent_pipeline=pipeline,
                    selection_count_24h=len(traces_24h),
                    selection_count_7d=len(traces_7d),
                    success_rate=success_rate,
                    avg_duration_ms=avg_duration,
                    fallback_count=0,
                )
            )

        # Build quality scores
        quality: list[RouteQuality] = []
        for usage in usages:
            score, factors = self._compute_quality(usage)
            quality.append(
                RouteQuality(
                    route_name=usage.route_name,
                    quality_score=score,
                    factors=factors,
                )
            )

        # Sort usages by count
        usages.sort(key=lambda u: u.selection_count_7d, reverse=True)
        most_used = usages[0].route_name if usages and usages[0].selection_count_7d > 0 else None
        lowest_q = min(quality, key=lambda q: q.quality_score, default=None)

        return RouteAnalyticsReport(
            routes=usages,
            fallbacks=[],  # fallback detection from trace metadata
            quality=quality,
            total_routes=len(all_routes),
            active_routes=len(active_routes),
            most_used_route=most_used,
            lowest_quality_route=lowest_q.route_name if lowest_q else None,
        )

    def _compute_quality(self, usage: RouteUsage) -> tuple[float, list[str]]:
        """Compute a quality score (0-100) for a route."""
        score = 100.0
        factors: list[str] = []

        # Penalize low success rate
        if usage.selection_count_7d > 0:
            if usage.success_rate < 0.8:
                penalty = (0.8 - usage.success_rate) * 100
                score -= penalty
                factors.append(f"Low success rate: {usage.success_rate:.1%}")

        # Penalize slow routes
        if usage.avg_duration_ms > 30000:
            penalty = min(30, (usage.avg_duration_ms - 30000) / 10000 * 10)
            score -= penalty
            factors.append(f"Slow: avg {usage.avg_duration_ms:.0f}ms")

        # Penalize high fallback rate
        if usage.selection_count_7d > 0 and usage.fallback_count > 0:
            fallback_rate = usage.fallback_count / usage.selection_count_7d
            if fallback_rate > 0.1:
                penalty = fallback_rate * 50
                score -= penalty
                factors.append(f"Fallback rate: {fallback_rate:.1%}")

        # Bonus for high usage (battle-tested)
        if usage.selection_count_7d > 50:
            factors.append("High usage (well-tested)")

        if not factors:
            factors.append("Healthy")

        return max(0.0, min(100.0, score)), factors

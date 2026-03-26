"""Agent analytics service — workload, failures, bottlenecks.

Provides analytics about agent performance and resource utilization
for the platform depth views.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.traces import ModelCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentWorkload:
    agent_name: str
    total_calls: int
    calls_last_24h: int
    calls_last_7d: int
    avg_duration_ms: float
    avg_tokens_per_call: float
    total_cost_usd: float
    error_count: int
    error_rate: float
    most_used_tools: list[str]
    recent_errors: list[str]


@dataclass(frozen=True)
class AgentBottleneck:
    agent_name: str
    bottleneck_type: str  # slow, high_error_rate, high_cost, overloaded
    severity: str  # low, medium, high
    detail: str
    metric_value: float


@dataclass(frozen=True)
class AgentAnalyticsReport:
    agents: list[AgentWorkload]
    bottlenecks: list[AgentBottleneck]
    total_calls_24h: int
    total_cost_24h: float
    busiest_agent: str | None
    highest_error_agent: str | None


class AgentAnalyticsService:
    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def get_report(self) -> AgentAnalyticsReport:
        """Build a full agent analytics report."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        # Get all model calls in the last 7 days
        result = await self._db.execute(
            select(ModelCall)
            .where(
                ModelCall.workspace_id == self._workspace_id,
                ModelCall.created_at >= week_ago,
            )
            .order_by(ModelCall.created_at.desc())
        )
        calls = result.scalars().all()

        # Group by agent
        agent_calls: dict[str, list[ModelCall]] = {}
        for call in calls:
            agent_calls.setdefault(call.agent_name, []).append(call)

        workloads: list[AgentWorkload] = []
        bottlenecks: list[AgentBottleneck] = []
        total_calls_24h = 0
        total_cost_24h = 0.0

        for agent_name, agent_call_list in agent_calls.items():
            calls_24h = [c for c in agent_call_list if c.created_at >= day_ago]
            calls_7d = agent_call_list

            total_calls_24h += len(calls_24h)
            total_cost_24h += sum(c.cost_usd for c in calls_24h)

            errors = [c for c in agent_call_list if c.error]
            error_rate = len(errors) / len(agent_call_list) if agent_call_list else 0.0

            # Collect tools
            all_tools: list[str] = []
            for c in agent_call_list:
                if c.tools_called:
                    all_tools.extend(c.tools_called)
            tool_counts: dict[str, int] = {}
            for t in all_tools:
                tool_counts[t] = tool_counts.get(t, 0) + 1
            most_used = sorted(tool_counts, key=tool_counts.get, reverse=True)[:5]

            avg_duration = (
                sum(c.duration_ms for c in agent_call_list) / len(agent_call_list)
                if agent_call_list
                else 0.0
            )
            avg_tokens = (
                sum(c.input_tokens + c.output_tokens for c in agent_call_list)
                / len(agent_call_list)
                if agent_call_list
                else 0.0
            )

            workloads.append(
                AgentWorkload(
                    agent_name=agent_name,
                    total_calls=len(agent_call_list),
                    calls_last_24h=len(calls_24h),
                    calls_last_7d=len(calls_7d),
                    avg_duration_ms=avg_duration,
                    avg_tokens_per_call=avg_tokens,
                    total_cost_usd=sum(c.cost_usd for c in agent_call_list),
                    error_count=len(errors),
                    error_rate=error_rate,
                    most_used_tools=most_used,
                    recent_errors=[e.error[:200] for e in errors[:3]],
                )
            )

            # Detect bottlenecks
            if avg_duration > 30000:  # >30s average
                bottlenecks.append(
                    AgentBottleneck(
                        agent_name=agent_name,
                        bottleneck_type="slow",
                        severity="high" if avg_duration > 60000 else "medium",
                        detail=f"Average response time: {avg_duration:.0f}ms",
                        metric_value=avg_duration,
                    )
                )
            if error_rate > 0.2:  # >20% error rate
                bottlenecks.append(
                    AgentBottleneck(
                        agent_name=agent_name,
                        bottleneck_type="high_error_rate",
                        severity="high" if error_rate > 0.5 else "medium",
                        detail=f"Error rate: {error_rate:.1%}",
                        metric_value=error_rate,
                    )
                )
            cost_7d = sum(c.cost_usd for c in agent_call_list)
            if cost_7d > 10.0:  # >$10/week
                bottlenecks.append(
                    AgentBottleneck(
                        agent_name=agent_name,
                        bottleneck_type="high_cost",
                        severity="high" if cost_7d > 50.0 else "medium",
                        detail=f"7-day cost: ${cost_7d:.2f}",
                        metric_value=cost_7d,
                    )
                )

        # Sort workloads by call count
        workloads.sort(key=lambda w: w.calls_last_24h, reverse=True)
        busiest = workloads[0].agent_name if workloads else None
        highest_error = max(
            (w for w in workloads if w.error_count > 0),
            key=lambda w: w.error_rate,
            default=None,
        )

        return AgentAnalyticsReport(
            agents=workloads,
            bottlenecks=bottlenecks,
            total_calls_24h=total_calls_24h,
            total_cost_24h=total_cost_24h,
            busiest_agent=busiest,
            highest_error_agent=highest_error.agent_name if highest_error else None,
        )
